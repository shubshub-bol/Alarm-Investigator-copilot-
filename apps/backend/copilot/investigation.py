"""Investigation playbooks: one per intent, each chaining MCP tool calls and document retrieval.

Every step feeds the next (asset id -> related assets -> correlation, top alarm -> trend -> latest alarm ->
priority and recommendations -> document searches built from what was found). A failed step is recorded
and the playbook carries on with what it has, so the answer can still be given with stated gaps.
"""

import logging
import re
import time
from datetime import UTC, datetime
from typing import Any

import anyio
from pydantic import BaseModel

from copilot.consistency import assess_actions
from copilot.mcp_client import McpToolClient
from copilot.trace import RetrievalRecord, RetrievedRef, ToolCallRecord
from copilot.understanding import QueryUnderstanding
from rag.retrieval import RetrievalFilters, RetrievedPassage, Retriever

log = logging.getLogger("copilot.investigation")

DEFAULT_LOOKBACK_DAYS = 90
MAX_PASSAGES = 8
MAX_PRIORITY_SCORING = 8
CORRELATION_LAG_MINUTES = 30
_CONFIDENCE_ORDER = ["none", "low", "medium", "high"]
_SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3, "critical": 4}


class ConversationState(BaseModel):
    """What a follow-up question ('this alarm') can refer back to."""

    last_asset: dict | None = None
    last_alarm: dict | None = None
    last_site: str | None = None
    last_unit: str | None = None


class Evidence(BaseModel):
    """Everything the investigation established, kept as structured data for the answer writer."""

    intent: str
    lookback_days: int | None = None
    asset: dict | None = None
    related_assets: list[dict] = []
    alarm_summary: dict | None = None
    top_alarm: dict | None = None
    trend: dict | None = None
    contributing_factors: list[dict] = []
    active_alarms: list[dict] = []
    ranked_alarms: list[dict] = []
    focus_alarm: dict | None = None
    priority: dict | None = None
    recommendations: dict | None = None
    action_checks: list[dict] = []  # per API action: the passages retrieved to check it
    passages: list[RetrievedPassage] = []
    passage_purposes: dict[str, str] = {}  # citation key -> why it was retrieved
    retrieval_confidence: str = "none"
    notes: list[str] = []  # assumptions, selections and partial failures, shown to the user
    missing: list[str] = []  # what could not be established


class InvestigationRun(BaseModel):
    evidence: Evidence
    steps: list[ToolCallRecord | RetrievalRecord]


class Investigation:
    def __init__(
        self,
        u: QueryUnderstanding,
        mcp: McpToolClient,
        retriever: Retriever | None,
        state: ConversationState,
        trace_id: str,
    ):
        self.u, self.mcp, self.retriever, self.state, self.trace_id = u, mcp, retriever, state, trace_id
        self.evidence = Evidence(intent=u.intent)
        self.steps: list[ToolCallRecord | RetrievalRecord] = []
        self.mcp_down = False
        self.lookback = u.lookback_days or DEFAULT_LOOKBACK_DAYS

    # =============================================================== primitives
    async def tool(self, name: str, purpose: str, **arguments: Any) -> dict | None:
        if self.mcp_down:
            return None
        args = {k: v for k, v in arguments.items() if v is not None}
        record = await self.mcp.call(name, args, trace_id=self.trace_id, purpose=purpose)
        record.step = len(self.steps) + 1
        self.steps.append(record)
        if record.status == "ok":
            return record.result
        if record.error_code == "MCP_UNAVAILABLE":
            self.mcp_down = True
            self.evidence.notes.append(
                "The MCP server is unavailable, so no live alarm data could be retrieved; "
                "the answer is based on documents only."
            )
        else:
            self.evidence.notes.append(
                f"Step {record.step} ({name}) failed: [{record.error_code}] {record.error_message}"
            )
        return None

    async def retrieve(
        self, query: str, purpose: str, filters: RetrievalFilters | None = None, k: int = 3
    ) -> list[RetrievedPassage]:
        filters = filters or RetrievalFilters()
        started, started_at = time.perf_counter(), datetime.now(UTC)
        common = {
            "purpose": purpose,
            "query": query,
            "filters": filters.model_dump(exclude_none=True, exclude_defaults=True),
            "trace_id": self.trace_id,
            "started_at": started_at,
            "step": len(self.steps) + 1,
        }
        if self.retriever is None:
            self.steps.append(
                RetrievalRecord(status="error", error_message="No retrieval index", duration_ms=0, **common)
            )
            if "The document index is not available." not in self.evidence.missing:
                self.evidence.missing.append("The document index is not available.")
            return []
        try:
            result = await anyio.to_thread.run_sync(lambda: self.retriever.search(query, k=k, filters=filters))
        except Exception as exc:  # noqa: BLE001 - a retrieval failure must not end the investigation
            self.steps.append(
                RetrievalRecord(
                    status="error",
                    error_message=f"{type(exc).__name__}: {exc}"[:200],
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                    **common,
                )
            )
            self.evidence.notes.append(f"Document search failed: {type(exc).__name__}")
            return []

        self.steps.append(
            RetrievalRecord(
                status="ok",
                confidence=result.confidence,
                top_dense_score=result.top_dense_score,
                results=[
                    RetrievedRef(
                        key=p.citation.key,
                        label=p.citation.label,
                        chunk_id=p.chunk_id,
                        dense_score=p.dense_score,
                        keyword_score=p.keyword_score,
                    )
                    for p in result.passages
                ],
                quarantined_withheld=result.quarantined_withheld,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                **common,
            )
        )
        if _CONFIDENCE_ORDER.index(result.confidence) > _CONFIDENCE_ORDER.index(self.evidence.retrieval_confidence):
            self.evidence.retrieval_confidence = result.confidence
        kept = []
        for passage in result.passages:
            existing = next((p for p in self.evidence.passages if p.chunk_id == passage.chunk_id), None)
            if existing:
                kept.append(existing)
                continue
            if len(self.evidence.passages) >= MAX_PASSAGES:
                continue
            key = f"S{len(self.evidence.passages) + 1}"  # renumber across searches
            renumbered = passage.model_copy(update={"citation": passage.citation.model_copy(update={"key": key})})
            self.evidence.passages.append(renumbered)
            self.evidence.passage_purposes[key] = purpose
            kept.append(renumbered)
        return kept

    # ============================================================ building blocks
    async def resolve_asset(self) -> dict | None:
        """Named asset -> search_assets. 'this alarm/asset' -> the asset from the previous answer."""
        u = self.u
        if u.asset_query:
            found = await self.tool(
                "search_assets", f"Resolve '{u.asset_query}' to an asset id", query=u.asset_query, limit=5
            )
            if found is None:
                return None
            results = found["results"]
            numbers = re.findall(r"\d{2,}", u.asset_query)
            top = results[0] if results else None
            if top is None or any(n not in f"{top['name']} {top['asset_id']}" for n in numbers):
                closest = f" Closest match: {top['name']}." if top else ""
                self.evidence.missing.append(f"No asset matches '{u.asset_query}'.{closest}")
                return None
            if len(results) > 1 and results[1]["match_score"] == top["match_score"]:
                self.evidence.notes.append(
                    f"'{u.asset_query}' matched several assets equally; used {top['name']} ({top['asset_id']})."
                )
            return top
        last = self.state.last_asset
        if u.uses_context and last and (not u.asset_type or last.get("asset_type") == u.asset_type):
            self.evidence.notes.append(f"Using {last['name']} from the previous question.")
            return last
        return None

    async def pick_asset_by_topic(self) -> dict | None:
        """No named asset: find assets of the mentioned type and pick the one with most matching alarms."""
        u = self.u
        if not u.asset_type:
            return None
        site, unit = u.site, u.unit
        found = await self.tool(
            "search_assets",
            f"Find {u.asset_type} assets",
            query=u.asset_type,
            asset_type=u.asset_type,
            site=site,
            unit=unit,
            limit=20,
        )
        if found and not found["results"]:  # the word may not be an exact asset_type (e.g. 'cooler')
            found = await self.tool(
                "search_assets", f"Find assets matching '{u.asset_type}'", query=u.asset_type, limit=20
            )
        candidates = (found or {}).get("results", [])
        if not candidates:
            if found is not None:
                self.evidence.missing.append(f"No {u.asset_type} assets found.")
            return None
        summary = await self.tool(
            "summarize_alarms",
            f"Compare alarm counts across {len(candidates)} {u.asset_type} assets",
            asset_ids=[c["asset_id"] for c in candidates],
            lookback_days=self.lookback,
            severity=u.severities,
            group_by=["asset_id", "asset_name", "alarm_code", "alarm_name"],
            kpis=["alarm_count"],
        )
        groups = (summary or {}).get("groups", [])
        if u.alarm_topic:
            groups = [g for g in groups if u.alarm_topic in g["key"]["alarm_name"].lower()]
        if not groups:
            topic = f" '{u.alarm_topic}'" if u.alarm_topic else ""
            self.evidence.missing.append(f"No{topic} alarms on {u.asset_type} assets in the last {self.lookback} days.")
            return None
        best = max(groups, key=lambda g: g["kpis"]["alarm_count"])
        chosen = next(c for c in candidates if c["asset_id"] == best["key"]["asset_id"])
        self.evidence.notes.append(
            f"Selected {chosen['name']}: {best['kpis']['alarm_count']} '{best['key']['alarm_name']}' alarms, "
            f"the most among {len(candidates)} {u.asset_type} assets in the last {self.lookback} days."
        )
        return chosen

    async def resolve_asset_or_pick(self) -> dict | None:
        """A named asset that does not exist is reported, never silently swapped for a different one."""
        asset = await self.resolve_asset()
        if asset is None and not self.u.asset_query:
            asset = await self.pick_asset_by_topic()
        return asset

    async def load_asset(self, asset_id: str) -> dict | None:
        meta = await self.tool("get_asset_metadata", "Asset details and related assets", asset_id=asset_id)
        if meta:
            keep = ("asset_id", "name", "asset_type", "site", "unit", "criticality", "manufacturer", "model")
            self.evidence.asset = {k: meta[k] for k in keep}
            self.evidence.related_assets = meta["related_assets"]
        return meta

    async def find_top_alarm(self, asset: dict) -> dict | None:
        u = self.u
        summary = await self.tool(
            "summarize_alarms",
            "Which alarms recur on this asset",
            asset_ids=[asset["asset_id"]],
            lookback_days=self.lookback,
            severity=u.severities,
            group_by=["alarm_code", "alarm_name"],
            kpis=["alarm_count", "critical_count", "recurring_rate", "avg_ack_delay"],
        )
        if summary is None:
            return None
        groups = summary["groups"]
        self.evidence.alarm_summary = {
            "time_range": summary["time_range"],
            "totals": summary["totals"],
            "groups": groups[:6],
        }
        if not groups:
            level = "/".join(u.severities) + " " if u.severities else ""
            self.evidence.missing.append(f"No {level}alarms on {asset['name']} in the last {self.lookback} days.")
            return None
        matching = [g for g in groups if u.alarm_topic and u.alarm_topic in g["key"]["alarm_name"].lower()]
        top = (matching or groups)[0]
        self.evidence.top_alarm = {**top["key"], **top["kpis"]}
        return self.evidence.top_alarm

    async def trend_for(self, asset: dict, code: str) -> None:
        trend = await self.tool(
            "get_alarm_trends",
            "Is this alarm getting more or less frequent",
            asset_ids=[asset["asset_id"]],
            alarm_codes=[code],
            bucket="weekly",
            lookback_days=self.lookback,
        )
        if trend:
            self.evidence.trend = {**trend["trend"], "bucket": "weekly", "points": len(trend["series"])}

    async def contributing_factors(self, asset: dict, code: str, min_support: int = 3) -> None:
        related = [a["asset_id"] for a in self.evidence.related_assets][:19]
        corr = await self.tool(
            "correlate_alarms",
            "Which alarms on this or related assets come first (likely contributing factors)",
            asset_ids=[asset["asset_id"], *related],
            lookback_days=self.lookback,
            lag_window_minutes=CORRELATION_LAG_MINUTES,
            min_support=min_support,
        )
        if corr is None:
            return
        factors = [
            p
            for p in corr["pairs"]
            if p["following"]["asset_id"] == asset["asset_id"]
            and p["following"]["alarm_code"] == code
            and p["relation"] == "precedes"
        ]
        self.evidence.contributing_factors = sorted(factors, key=lambda p: -p["confidence"])[:3]
        if not factors:
            self.evidence.missing.append(
                "No alarm consistently preceded the main alarm (no clear contributing factor)."
            )

    async def latest_alarm(self, asset: dict, code: str) -> dict | None:
        found = await self.tool(
            "get_alarms",
            "Most recent occurrence of this alarm",
            asset_id=asset["asset_id"],
            alarm_codes=[code],
            page_size=1,
            sort_order="desc",
        )
        alarms = (found or {}).get("alarms", [])
        if alarms:
            self.evidence.focus_alarm = alarms[0]
        return alarms[0] if alarms else None

    async def score(self, alarm: dict) -> dict | None:
        result = await self.tool("score_alarm_priority", "How urgent is this alarm", alarm_id=alarm["alarm_id"])
        if result is None:
            return None
        return {
            "alarm_id": alarm["alarm_id"],
            "asset_id": alarm["asset_id"],
            "asset_name": alarm["asset_name"],
            "alarm_code": alarm["alarm_code"],
            "alarm_name": alarm["alarm_name"],
            "severity": alarm["severity"],
            "score": result["score"],
            "priority": result["priority"],
            "priority_label": result["priority_label"],
            "factors": result["factors"],
        }

    async def rank(self, alarms: list[dict]) -> None:
        ordered = sorted(alarms, key=lambda a: (-_SEVERITY_ORDER[a["severity"]], a["start_time"]), reverse=False)
        if len(ordered) > MAX_PRIORITY_SCORING:
            self.evidence.notes.append(
                f"Scored the {MAX_PRIORITY_SCORING} most severe of {len(ordered)} active alarms."
            )
        scored = [s for a in ordered[:MAX_PRIORITY_SCORING] if (s := await self.score(a))]
        scored.sort(key=lambda s: -s["score"])
        self.evidence.ranked_alarms = scored
        if scored:
            best = scored[0]
            self.evidence.priority = best
            self.evidence.focus_alarm = next(a for a in alarms if a["alarm_id"] == best["alarm_id"])

    async def recommend(self, alarm_id: str) -> dict | None:
        recs = await self.tool(
            "get_operator_recommendations", "The Alarm API's recommended actions for this alarm", alarm_id=alarm_id
        )
        if recs:
            self.evidence.recommendations = {
                k: recs[k]
                for k in (
                    "likely_causes",
                    "recommended_actions",
                    "source",
                    "related_assets",
                    "asset_context",
                    "historical_pattern",
                )
            }
            if self.evidence.priority is None:
                self.evidence.priority = {**recs["priority"], "alarm_id": alarm_id}
        return recs

    # ------------------------------------------------------------- RAG helpers
    def _asset_filters(self, codes: list[str]) -> RetrievalFilters:
        asset = self.evidence.asset or {}
        return RetrievalFilters(alarm_codes=codes or None, site=asset.get("site"))

    async def rag_for_alarm(self, alarm_name: str, code: str, k: int = 3) -> None:
        asset_type = (self.evidence.asset or {}).get("asset_type", "")
        await self.retrieve(
            f"{asset_type} {alarm_name} ({code})".strip(),  # plain wording ranked best across alarm types
            f"Procedure for {alarm_name} ({code})",
            self._asset_filters([code]),
            k=k,
        )

    async def rag_for_factors(self) -> None:
        top = self.evidence.top_alarm or {}
        asset_type = (self.evidence.asset or {}).get("asset_type", "")
        for factor in self.evidence.contributing_factors[:1]:
            lead = factor["leading"]
            await self.retrieve(
                f"{lead['alarm_name']} before {asset_type} {top.get('alarm_name', '')}: cause and maintenance checks",
                f"Guidance on contributing factor: {lead['asset_name']} {lead['alarm_name']}",
                RetrievalFilters(  # the "why" lives in maintenance and troubleshooting knowledge, not procedures
                    alarm_codes=[lead["alarm_code"], top.get("alarm_code", "")],
                    doc_types=["maintenance_manual", "troubleshooting_guide", "knowledge_article"],
                ),
                k=2,
            )

    async def rag_for_safety(self) -> None:
        asset_type = (self.evidence.asset or {}).get("asset_type", "equipment")
        await self.retrieve(
            f"lockout and isolation before opening or inspecting a {asset_type}",
            "Safety requirements before field inspection",
            RetrievalFilters(doc_types=["safety_instruction"]),
            k=1,
        )

    async def rag_question(self) -> None:
        await self.retrieve(self.u.question or self.u.rationale, "Search the documents for the question", k=4)

    # ================================================================ playbooks
    async def investigate_asset(self) -> None:
        if self.u.lookback_days is None:
            self.evidence.notes.append(f"No time window given; used the last {self.lookback} days of data.")
        self.evidence.lookback_days = self.lookback
        asset = await self.resolve_asset_or_pick()
        if asset is None:
            return await self.rag_question()
        await self.load_asset(asset["asset_id"])
        asset = self.evidence.asset or asset
        top = await self.find_top_alarm(asset)
        if top:
            await self.trend_for(asset, top["alarm_code"])
            await self.contributing_factors(asset, top["alarm_code"])
            alarm = await self.latest_alarm(asset, top["alarm_code"])
            if alarm:
                self.evidence.priority = await self.score(alarm)
                await self.recommend(alarm["alarm_id"])
            await self.rag_for_alarm(top["alarm_name"], top["alarm_code"])
            await self.rag_for_factors()
        else:
            await self.rag_question()
        await self.rag_for_safety()

    async def active_alarms(self) -> None:
        u = self.u
        asset = await self.resolve_asset()
        site, unit = (u.site or self.state.last_site, u.unit or self.state.last_unit) if not asset else (None, None)
        if asset is None and not (site or unit):
            if not self.evidence.missing:
                self.evidence.missing.append("Name an asset, site or unit to list its active alarms.")
            return await self.rag_question()
        found = await self.tool(
            "get_alarms",
            "Alarms that are active now",
            asset_id=asset["asset_id"] if asset else None,
            site=site,
            unit=unit,
            status="active",
            severity=u.severities,
            page_size=50,
        )
        if asset:
            await self.load_asset(asset["asset_id"])
        if found is None:
            return await self.rag_question()
        self.evidence.active_alarms = found["alarms"]
        if not found["alarms"]:
            level = "/".join(u.severities) + " " if u.severities else ""
            where = (self.evidence.asset or {}).get("name") or " ".join(x for x in (site, unit) if x)
            self.evidence.missing.append(f"There are no active {level}alarms on {where}.")
            return
        await self.rank(found["alarms"])
        focus = self.evidence.focus_alarm
        if focus:
            await self.recommend(focus["alarm_id"])
            if self.evidence.asset is None:
                await self.load_asset(focus["asset_id"])
            await self.rag_for_alarm(focus["alarm_name"], focus["alarm_code"])
            others = {(a["alarm_code"], a["alarm_name"]) for a in found["alarms"]} - {
                (focus["alarm_code"], focus["alarm_name"])
            }
            for code, name in sorted(others)[:1]:
                await self.rag_for_alarm(name, code, k=2)
        await self.rag_for_safety()

    async def highest_priority(self) -> None:
        u = self.u
        site, unit = u.site or self.state.last_site, u.unit or self.state.last_unit
        asset = await self.resolve_asset() if u.asset_query else None
        found = await self.tool(
            "get_alarms",
            "All active alarms in scope",
            asset_id=asset["asset_id"] if asset else None,
            site=None if asset else site,
            unit=None if asset else unit,
            status="active",
            fetch_all=True,
            max_items=200,
        )
        if found is None:
            return await self.rag_question()
        if not found["alarms"]:
            where = " ".join(x for x in (site, unit) if x) or "the plant"
            self.evidence.missing.append(f"There are no active alarms in {where}.")
            return
        self.evidence.active_alarms = found["alarms"]
        await self.rank(found["alarms"])
        focus = self.evidence.focus_alarm
        if focus:
            await self.load_asset(focus["asset_id"])
            await self.recommend(focus["alarm_id"])
            await self.retrieve(
                "alarm priority definitions and required response times",
                "What the priority means under the alarm philosophy",
                RetrievalFilters(doc_types=["alarm_philosophy"]),
                k=2,
            )
            await self.rag_for_alarm(focus["alarm_name"], focus["alarm_code"])

    async def related_assets(self) -> None:
        self.evidence.lookback_days = self.lookback
        asset = await self.resolve_asset_or_pick()
        if asset is None:
            return await self.rag_question()
        await self.load_asset(asset["asset_id"])
        asset = self.evidence.asset or asset
        top = await self.find_top_alarm(asset)
        if top:
            await self.contributing_factors(asset, top["alarm_code"], min_support=2)
            alarm = await self.latest_alarm(asset, top["alarm_code"])
            if alarm:
                await self.recommend(alarm["alarm_id"])
            await self.retrieve(
                f"related assets to inspect after a {asset['asset_type']} {top['alarm_name']}",
                f"Which related equipment the documents say to check after {top['alarm_name']}",
                RetrievalFilters(alarm_codes=[top["alarm_code"]]),
                k=3,
            )
            await self.rag_for_factors()
        await self.rag_for_safety()

    async def alarm_from_context_or_asset(self) -> dict | None:
        """The alarm a follow-up refers to: the previous answer's alarm, or the top alarm of a named asset."""
        last = self.state.last_alarm
        if self.u.uses_context and last and not self.u.asset_query:
            detail = await self.tool(
                "get_alarm_details", "Reload the alarm from the previous answer", alarm_id=last["alarm_id"]
            )
            if detail:
                self.evidence.focus_alarm = {k: v for k, v in detail.items() if k not in ("asset", "meta")}
                self.evidence.asset = detail["asset"]
            return self.evidence.focus_alarm
        asset = await self.resolve_asset()
        if asset is None:
            if not self.evidence.missing:
                self.evidence.missing.append("No alarm in context; ask about a specific asset or alarm first.")
            return None
        await self.load_asset(asset["asset_id"])
        top = await self.find_top_alarm(self.evidence.asset or asset)
        return await self.latest_alarm(self.evidence.asset or asset, top["alarm_code"]) if top else None

    async def procedure_lookup(self) -> None:
        alarm = await self.alarm_from_context_or_asset()
        if alarm is None:
            return await self.rag_question()
        asset_type = (self.evidence.asset or {}).get("asset_type", "")
        await self.retrieve(
            f"{asset_type} {alarm['alarm_name']} ({alarm['alarm_code']}) operating procedure",
            f"Approved operating procedure for {alarm['alarm_name']}",
            RetrievalFilters(
                doc_types=["operating_procedure"],
                alarm_codes=[alarm["alarm_code"]],
                site=(self.evidence.asset or {}).get("site"),
            ),
            k=2,
        )
        await self.rag_for_alarm(alarm["alarm_name"], alarm["alarm_code"], k=3)

    async def recommendation_check(self) -> None:
        alarm = await self.alarm_from_context_or_asset()
        if alarm is None:
            return await self.rag_question()
        recs = await self.recommend(alarm["alarm_id"])
        if recs is None:
            return await self.rag_for_alarm(alarm["alarm_name"], alarm["alarm_code"])
        if self.evidence.asset is None and recs.get("asset_context"):
            self.evidence.asset = recs["asset_context"]
        asset_type = (self.evidence.asset or {}).get("asset_type", "")
        for step in recs["recommended_actions"][:4]:
            passages = await self.retrieve(
                f"{asset_type} {alarm['alarm_name']}: {step['action']}",
                f"Check API action {step['step']} against the documents",
                self._asset_filters([alarm["alarm_code"]]),
                k=2,
            )
            self.evidence.action_checks.append({**step, "passage_keys": [p.citation.key for p in passages]})

    async def knowledge_question(self) -> None:
        await self.rag_question()

    async def governance_if_conflict(self) -> None:
        """If the API's advice contradicts a retrieved document, also fetch the rule on which one governs."""
        recs = self.evidence.recommendations
        if not recs or not self.evidence.passages:
            return
        if any(a.verdict == "conflict" for a in assess_actions(recs["recommended_actions"], self.evidence.passages)):
            await self.retrieve(
                "recommendation conflicts with an approved operating procedure, which one governs",
                "Which guidance governs when advice conflicts",
                RetrievalFilters(doc_types=["alarm_philosophy"]),
                k=1,
            )

    # ====================================================================== run
    async def run(self) -> InvestigationRun:
        playbook = getattr(self, self.u.intent)
        await playbook()
        await self.governance_if_conflict()
        ev = self.evidence
        if ev.asset:
            self.state.last_asset = ev.asset
        if ev.focus_alarm:
            self.state.last_alarm = ev.focus_alarm
        self.state.last_site = self.u.site or self.state.last_site
        self.state.last_unit = self.u.unit or self.state.last_unit
        log.info(
            "investigation finished",
            extra={
                "fields": {
                    "trace_id": self.trace_id,
                    "intent": self.u.intent,
                    "steps": len(self.steps),
                    "failed_steps": sum(s.status != "ok" for s in self.steps),
                    "passages": len(ev.passages),
                }
            },
        )
        return InvestigationRun(evidence=ev, steps=self.steps)


async def investigate(
    u: QueryUnderstanding,
    mcp: McpToolClient,
    retriever: Retriever | None,
    state: ConversationState,
    *,
    trace_id: str,
) -> InvestigationRun:
    return await Investigation(u, mcp, retriever, state, trace_id).run()
