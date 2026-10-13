"""The evidence record: what the investigation established, filled from every tool result.

Every MCP call, whether a playbook or the agent made it, passes through Investigation.call_tool, which hands each
successful result to `record_result`. One rule per tool writes what that result plainly shows. Playbooks add only
their judgements: the top alarm, the focus alarm, the ranking, which contributing factors are strong enough.
"""

from typing import Any

from pydantic import BaseModel

from rag.retrieval import RetrievedPassage

ASSET_FIELDS = ("asset_id", "name", "asset_type", "site", "unit", "criticality", "manufacturer", "model")
RECOMMENDATION_FIELDS = (
    "likely_causes",
    "recommended_actions",
    "source",
    "related_assets",
    "asset_context",
    "historical_pattern",
)
PRIORITY_FIELDS = ("alarm_id", "score", "priority", "priority_label", "factors")


class NegativeFinding(BaseModel):
    """A lookup that worked and found nothing: a fact like any other, tied to the step that established it."""

    text: str
    step: int
    tool: str  # the MCP tool, or "search_documents" for a document search


class Evidence(BaseModel):
    """Everything the investigation established, kept as structured data for the answer writer."""

    intent: str
    lookback_days: int | None = None
    asset: dict | None = None  # the asset the question is about, when the calls clearly focus on one
    related_assets: list[dict] = []
    # the assets a search, asset details, alarm details or the API's advice returned, by asset id (the related assets
    # listed in an asset's details are not added: they are named there, not looked up)
    known_assets: dict[str, dict] = {}
    focused_asset_ids: list[str] = []  # the assets single-asset calls were about, in order
    alarm_summary: dict | None = None
    top_alarm: dict | None = None
    trend: dict | None = None
    contributing_factors: list[dict] = []
    active_alarms: list[dict] = []
    ranked_alarms: list[dict] = []
    focus_alarm: dict | None = None
    priority: dict | None = None
    recommendations: dict | None = None
    recommendations_step: int | None = None  # the trace step whose result is `recommendations`
    advised_alarm_ids: list[str] = []  # the alarms the API's advice was fetched for, in order
    action_checks: list[dict] = []  # per API action: the passages retrieved to check it
    passages: list[RetrievedPassage] = []
    passage_purposes: dict[str, str] = {}  # citation key -> why it was retrieved
    retrieval_confidence: str = "none"
    negatives: list[NegativeFinding] = []  # lookups that found nothing: facts, not gaps
    notes: list[str] = []  # assumptions, selections and partial failures, shown to the user
    missing: list[str] = []  # what could not be looked up or failed: a service down, an error, a missing input


def _asset(info: dict) -> dict:
    return {k: info[k] for k in ASSET_FIELDS if k in info}


def _know(ev: Evidence, info: dict | None) -> None:
    if info and info.get("asset_id"):
        ev.known_assets[info["asset_id"]] = {**ev.known_assets.get(info["asset_id"], {}), **_asset(info)}
        if len(ev.focused_asset_ids) == 1:  # the one asset calls were about may have only now become known
            ev.asset = ev.known_assets.get(ev.focused_asset_ids[0])


def _focus(ev: Evidence, asset_id: str | None) -> None:
    """A call about exactly one asset. While every such call is about the same asset, it is the question's asset;
    calls about two different assets (a comparison) leave it open."""
    if not asset_id:
        return
    if asset_id not in ev.focused_asset_ids:
        ev.focused_asset_ids.append(asset_id)
    only = ev.focused_asset_ids[0] if len(ev.focused_asset_ids) == 1 else None
    ev.asset = ev.known_assets.get(only) if only else None


def _one(ids: Any) -> str | None:
    return ids[0] if isinstance(ids, list) and len(ids) == 1 else None


def _by_asset(arguments: dict[str, Any]) -> bool:
    """A summary grouped per asset compares assets, even when it covers only one."""
    group_by = arguments.get("group_by")
    return isinstance(group_by, list) and "asset_id" in group_by


def clear_subject(ev: Evidence) -> None:
    """Forget what the calls so far established about the question's subject, for a playbook that starts now and
    investigates its own: the asset and what hangs on it, the alarm, the advice. What was found about the plant
    stays: the known assets, the passages, the findings that a lookup found nothing, the notes and the gaps."""
    ev.asset = ev.focus_alarm = ev.priority = ev.recommendations = ev.alarm_summary = ev.trend = None
    ev.recommendations_step = None
    ev.related_assets, ev.focused_asset_ids, ev.advised_alarm_ids, ev.active_alarms = [], [], [], []


def record_result(ev: Evidence, tool: str, arguments: dict[str, Any], result: dict[str, Any], step: int) -> None:
    """Write what one successful tool result plainly shows into the evidence record. `step` is the trace step that
    made the call, so a fact can be tied to the advice it states."""
    if tool == "search_assets":
        for found in result.get("results", []):
            _know(ev, found)
    elif tool == "get_asset_metadata":
        _know(ev, result)
        ev.related_assets = result.get("related_assets", [])
        _focus(ev, result.get("asset_id"))
    elif tool == "get_alarm_details":
        _know(ev, result.get("asset"))
        if ev.focus_alarm is None:
            ev.focus_alarm = {k: v for k, v in result.items() if k not in ("asset", "meta")}
        _focus(ev, result.get("asset_id"))
    elif tool == "get_alarms":
        if arguments.get("status") == "active":
            ev.active_alarms = result.get("alarms", [])
        _focus(ev, arguments.get("asset_id"))
    elif tool == "summarize_alarms":
        asset_id = _one(arguments.get("asset_ids"))
        if asset_id and not _by_asset(arguments):
            ev.alarm_summary = {
                "time_range": result["time_range"],
                "totals": result["totals"],
                "groups": result["groups"][:6],
            }
            _focus(ev, asset_id)
    elif tool == "get_alarm_trends":
        asset_id = _one(arguments.get("asset_ids"))
        if asset_id:
            ev.trend = {**result["trend"], "bucket": result.get("bucket"), "points": len(result.get("series", []))}
        _focus(ev, asset_id)
    elif tool == "score_alarm_priority":
        if ev.priority is None:
            ev.priority = {k: result[k] for k in PRIORITY_FIELDS if k in result}
    elif tool == "get_operator_recommendations":
        alarm_id = result.get("alarm_id", arguments.get("alarm_id"))
        if alarm_id and alarm_id not in ev.advised_alarm_ids:
            ev.advised_alarm_ids.append(alarm_id)
        if len(ev.advised_alarm_ids) > 1:  # advice for two different alarms: neither is "the" advice, and it stays so
            ev.recommendations, ev.recommendations_step = None, None
        else:
            ev.recommendations = {k: result[k] for k in RECOMMENDATION_FIELDS if k in result}
            ev.recommendations_step = step
        if ev.priority is None and result.get("priority"):
            ev.priority = {**result["priority"], "alarm_id": alarm_id}
        context = result.get("asset_context")
        _know(ev, context)
        _focus(ev, (context or {}).get("asset_id"))
