# Evidence integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close three design gaps the first full model run exposed: one evidence record filled by every tool call, "nothing found" as citable facts with no claim allowed without evidence, and an exam that judges outcomes rather than routes.

**Architecture:** A new `copilot/evidence.py` owns the `Evidence` record and `record_result`, called by `Investigation.call_tool` after every successful tool call (playbook or agent). Lookups that find nothing become `NegativeFinding`s, emitted by `build_facts` as facts (`Fact.negative`); the agent states empty tool results and empty document searches as facts too. The agent and the answer checks refuse a claim with no evidence unless it is `cannot_answer`. The exam checks the intent only on the rules path.

**Tech Stack:** Python 3.12, pydantic 2, pytest (anyio), ruff, mypy.

**Spec:** `docs/specs/2026-10-13-evidence-integrity-design.md`

## Global Constraints

- One recorder at the single door every tool call passes through (`Investigation.call_tool`); playbooks keep only their judgements (top alarm, focus alarm, ranking, which contributing factors are strong enough, notes).
- The question's asset is set only when the calls clearly focus on one asset; calls about two different assets leave it open; a search with several results never sets it.
- A lookup that succeeds and finds nothing is a fact with its own key, tied to its trace step; findings about alarms state the count as the number 0 (not "no").
- Gaps are only what could not be looked up or failed: a service down, an error, a missing input.
- With nothing looked up, the only valid answer is `cannot_answer`; the agent refuses "done" once, and the checks reject an evidence-free claim with the reason `no evidence was looked up`.
- The exam checks the intent only on the rules path (`understanding.method == "rules"`).
- Without a model the copilot behaves as today (offline set 34/34) except that negative findings are cited facts.
- Line length 120; ruff rules `E, F, I, B, UP, S`; no new dependencies; never call a real language model in tests.
- Run pytest as `uv run python -m pytest ... -p no:cacheprovider` and mypy as `uv run python -m mypy` (the `.exe` launchers are blocked on this PC).
- Write Python with the Write/Edit tools, not shell heredocs. Before each commit: no backspace bytes in changed `.py` files, and `git ls-files --eol | grep -c i/crlf` is 0.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; stage only the task's files.

## Review Focus

- A comparison: the agent looks up two different assets. The record's asset stays empty, never the wrong one (Task 1 test `test_a_comparison_leaves_the_questions_asset_open`).
- A lookup that failed must not read as "found nothing": a failed alarm summary in the type-based playbook is a failure note, not "There are 0 ... alarms" (Task 2 test `test_a_failed_summary_is_not_a_finding`).
- Saved chats from before the change still load (their evidence lacks the new fields) (Task 1 test `test_a_saved_chat_from_before_still_loads`).
- The model keeps answering without evidence after the one refusal: its text is rejected and the plain summary is shown (Task 3 test `test_a_claim_without_evidence_is_not_shown`).
- A follow-up after an agent answer that used only tools: the chat state knows the asset (Task 1 test `test_the_agents_own_lookups_fill_the_evidence_record`).

---

### Task 1: One evidence record

**Files:**
- Create: `apps/backend/copilot/evidence.py`
- Modify: `apps/backend/copilot/investigation.py` (`Evidence` moves out; `call_tool` records; playbooks stop copying)
- Modify: `apps/backend/copilot/service.py`, `apps/backend/copilot/context.py` (import `Evidence` from `copilot.evidence`)
- Test: `apps/backend/tests/test_evidence.py` (new), `apps/backend/tests/test_agent.py`

**Interfaces:**
- Produces: `copilot.evidence.Evidence` (all current fields plus `known_assets: dict[str, dict] = {}`, `focused_asset_ids: list[str] = []`); `copilot.evidence.record_result(ev: Evidence, tool: str, arguments: dict[str, Any], result: dict[str, Any]) -> None`; `copilot.evidence.ASSET_FIELDS`. `copilot.investigation` still exposes `Evidence` (imported from `copilot.evidence`).

- [ ] **Step 1: Write the failing tests**

Create `apps/backend/tests/test_evidence.py`:

```python
"""The evidence record: what each tool result writes into it, whoever made the call."""

from copilot.evidence import Evidence, record_result

PUMP = {
    "asset_id": "AST-BFP-101",
    "name": "Boiler Feed Pump 101",
    "asset_type": "pump",
    "site": "NorthPlant",
    "unit": "Unit 1",
    "criticality": "high",
}
OTHER = {**PUMP, "asset_id": "AST-BFP-102", "name": "Boiler Feed Pump 102"}


def fresh():
    return Evidence(intent="agent")


def test_a_search_makes_assets_known_but_chooses_none():
    ev = fresh()
    record_result(ev, "search_assets", {"query": "pump"}, {"results": [{**PUMP, "match_score": 0.9}, OTHER]})
    assert set(ev.known_assets) == {"AST-BFP-101", "AST-BFP-102"}
    assert "match_score" not in ev.known_assets["AST-BFP-101"]
    assert ev.asset is None  # a search with several results never sets the question's asset


def test_a_call_about_one_known_asset_sets_the_questions_asset():
    ev = fresh()
    record_result(ev, "search_assets", {"query": "Boiler Feed Pump 101"}, {"results": [PUMP]})
    record_result(ev, "get_alarms", {"asset_id": "AST-BFP-101", "status": "active"}, {"alarms": []})
    assert ev.asset == PUMP
    assert ev.active_alarms == []


def test_metadata_gives_the_asset_and_its_related_assets():
    ev = fresh()
    meta = {**PUMP, "manufacturer": "Sulzer", "model": "HPT-200", "install_date": "2014", "related_assets": [OTHER]}
    record_result(ev, "get_asset_metadata", {"asset_id": "AST-BFP-101"}, meta)
    assert ev.asset == {**PUMP, "manufacturer": "Sulzer", "model": "HPT-200"}
    assert ev.related_assets == [OTHER]


def test_calls_about_two_different_assets_leave_the_questions_asset_open():
    ev = fresh()
    record_result(ev, "get_asset_metadata", {"asset_id": "AST-BFP-101"}, {**PUMP, "related_assets": []})
    record_result(ev, "get_asset_metadata", {"asset_id": "AST-BFP-102"}, {**OTHER, "related_assets": []})
    assert ev.asset is None  # a comparison: neither pump is "the" asset


def test_only_active_alarm_queries_set_the_active_list():
    ev = fresh()
    alarm = {"alarm_id": "ALM-1", "asset_id": "AST-BFP-101"}
    record_result(ev, "get_alarms", {"asset_id": "AST-BFP-101", "page_size": 1}, {"alarms": [alarm]})
    assert ev.active_alarms == []
    record_result(ev, "get_alarms", {"site": "NorthPlant", "status": "active"}, {"alarms": [alarm]})
    assert ev.active_alarms == [alarm]


def test_a_summary_and_a_trend_of_one_asset_are_recorded_but_not_a_comparison():
    ev = fresh()
    groups = [{"key": {"alarm_code": f"C{n}"}, "kpis": {"alarm_count": n}} for n in range(8)]
    summary = {"time_range": {"start_time": "a", "end_time": "b"}, "totals": {"alarm_count": 28}, "groups": groups}
    record_result(ev, "summarize_alarms", {"asset_ids": ["AST-BFP-101", "AST-BFP-102"]}, summary)
    assert ev.alarm_summary is None  # a comparison across assets
    record_result(ev, "summarize_alarms", {"asset_ids": ["AST-BFP-101"]}, summary)
    assert ev.alarm_summary == {"time_range": summary["time_range"], "totals": {"alarm_count": 28}, "groups": groups[:6]}
    trend = {
        "bucket": "weekly",
        "series": [{}, {}],
        "trend": {"direction": "increasing", "first_half_avg": 1.0, "second_half_avg": 2.0, "change_percent": 100.0},
    }
    record_result(ev, "get_alarm_trends", {"asset_ids": ["AST-BFP-101"], "bucket": "weekly"}, trend)
    assert ev.trend == {**trend["trend"], "bucket": "weekly", "points": 2}


def test_the_apis_advice_its_priority_and_its_asset_are_recorded():
    ev = fresh()
    recs = {
        "alarm_id": "ALM-1",
        "priority": {"score": 92.0, "priority": "P1", "priority_label": "Critical"},
        "likely_causes": ["wear"],
        "recommended_actions": [{"step": 1, "action": "Check"}],
        "source": "rules",
        "related_assets": [],
        "asset_context": {**PUMP, "install_date": "2014"},
        "historical_pattern": None,
    }
    record_result(ev, "get_operator_recommendations", {"alarm_id": "ALM-1"}, recs)
    assert ev.recommendations["recommended_actions"] == [{"step": 1, "action": "Check"}]
    assert ev.priority == {"score": 92.0, "priority": "P1", "priority_label": "Critical", "alarm_id": "ALM-1"}
    assert ev.asset == PUMP


def test_a_score_sets_the_priority_only_when_none_is_known():
    ev = fresh()
    score = {"alarm_id": "ALM-1", "score": 80.0, "priority": "P2", "priority_label": "High", "factors": [], "alarm": {}}
    record_result(ev, "score_alarm_priority", {"alarm_id": "ALM-1"}, score)
    assert ev.priority == {"alarm_id": "ALM-1", "score": 80.0, "priority": "P2", "priority_label": "High", "factors": []}
    record_result(ev, "score_alarm_priority", {"alarm_id": "ALM-2"}, {**score, "alarm_id": "ALM-2", "score": 99.0})
    assert ev.priority["alarm_id"] == "ALM-1"  # which alarm matters most is a playbook's judgement


def test_an_alarms_details_give_the_focus_alarm_and_its_asset():
    ev = fresh()
    detail = {"alarm_id": "ALM-1", "asset_id": "AST-BFP-101", "alarm_name": "High Vibration", "asset": PUMP, "meta": {}}
    record_result(ev, "get_alarm_details", {"alarm_id": "ALM-1"}, detail)
    assert ev.focus_alarm == {"alarm_id": "ALM-1", "asset_id": "AST-BFP-101", "alarm_name": "High Vibration"}
    assert ev.asset == PUMP


def test_other_tools_record_nothing_beyond_their_fact():
    ev = fresh()
    record_result(ev, "analyze_alarm_floods", {"asset_ids": ["AST-BFP-101"]}, {"flood_windows": []})
    assert ev == fresh()


def test_a_saved_chat_from_before_still_loads():
    old = {"intent": "investigate_asset", "asset": PUMP}  # saved before known_assets existed
    assert Evidence.model_validate(old).known_assets == {}
```

Append to `apps/backend/tests/test_agent.py` (it already has `ScriptedLlm`, `tool`, `done`, `run_agent`, `ConversationState`):

```python
async def test_the_agents_own_lookups_fill_the_evidence_record(mcp, retriever):
    state = ConversationState()
    result, _ = await run_agent(
        "Boiler B-201 how many times it failed in past?",
        tool("search_assets", query="Boiler B-201"),
        tool("get_alarms", asset_id="AST-BLR-201"),
        done("Boiler B-201 had 24 alarms [T2].", "count"),
        mcp=mcp,
        retriever=retriever,
        state=state,
    )
    assert result.run.evidence.asset["asset_id"] == "AST-BLR-201"
    assert state.last_asset["asset_id"] == "AST-BLR-201"  # a follow-up ("this boiler") can refer back to it


async def test_a_comparison_leaves_the_questions_asset_open(mcp, retriever):
    result, _ = await run_agent(
        "Compare Boiler Feed Pump 101 and Boiler Feed Pump 102",
        tool("get_asset_metadata", asset_id="AST-BFP-101"),
        tool("get_asset_metadata", asset_id="AST-BFP-102"),
        done("Both are high-criticality pumps [T1] [T2].", "compare"),
        mcp=mcp,
        retriever=retriever,
    )
    assert result.run.evidence.asset is None


async def test_a_recipe_lists_only_the_advice_it_fetched(mcp, retriever):
    """The agent's own advice lookup now fills the record too; a later recipe must not present it as its own."""
    question = "Which procedure applies to Gas Compressor C-301 discharge pressure alarms?"
    _, llm = await run_agent(
        question,
        tool("get_operator_recommendations", alarm_id="ALM-000418"),  # a Gas Compressor C-301 alarm
        recipe("procedure_lookup", asset="Gas Compressor C-301", alarm_topic="discharge pressure"),
        done("SOP-CMP-004 applies [S1].", "document"),
        mcp=mcp,
        retriever=retriever,
    )
    assert "API RECOMMENDED ACTIONS TO ASSESS" not in llm.prompts[2]  # procedure_lookup fetches no advice


async def test_a_recipe_investigates_its_own_asset(mcp, retriever):
    """An earlier look at another asset must not leave the recipe without one (its document filters need it)."""
    result, _ = await run_agent(
        "Investigate Boiler Feed Pump 101",
        tool("get_asset_metadata", asset_id="AST-CMP-301"),
        recipe("investigate_asset", asset="Boiler Feed Pump 101"),
        done("High vibration recurs [T2].", "why"),
        mcp=mcp,
        retriever=retriever,
    )
    ev = result.run.evidence
    assert ev.asset["asset_id"] == "AST-BFP-101"
    assert "SOP-BFP-001 §4.1" in {p.citation.label for p in ev.passages}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run python -m pytest apps/backend/tests/test_evidence.py apps/backend/tests/test_agent.py -p no:cacheprovider -k "evidence or record or comparison or saved_chat or search_makes or call_about or metadata_gives or two_different or active_alarm_queries or summary_and or advice or score_sets or details_give or other_tools"`
Expected: ERROR `No module named 'copilot.evidence'` for test_evidence.py; the two evidence-record agent tests FAIL (`asset` is None for B-201). `test_a_recipe_lists_only_the_advice_it_fetched` and `test_a_recipe_investigates_its_own_asset` pass now (only playbooks fill the record today) and must still pass after Step 3: they guard the `_recipe` change below.

- [ ] **Step 3: Implement**

Create `apps/backend/copilot/evidence.py`:

```python
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


class Evidence(BaseModel):
    """Everything the investigation established, kept as structured data for the answer writer."""

    intent: str
    lookback_days: int | None = None
    asset: dict | None = None  # the asset the question is about, when the calls clearly focus on one
    related_assets: list[dict] = []
    known_assets: dict[str, dict] = {}  # every asset a tool returned, by asset id
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
    action_checks: list[dict] = []  # per API action: the passages retrieved to check it
    passages: list[RetrievedPassage] = []
    passage_purposes: dict[str, str] = {}  # citation key -> why it was retrieved
    retrieval_confidence: str = "none"
    notes: list[str] = []  # assumptions, selections and partial failures, shown to the user
    missing: list[str] = []  # what could not be looked up or failed: a service down, an error, a missing input


def _asset(info: dict) -> dict:
    return {k: info[k] for k in ASSET_FIELDS if k in info}


def _know(ev: Evidence, info: dict | None) -> None:
    if info and info.get("asset_id"):
        ev.known_assets[info["asset_id"]] = {**ev.known_assets.get(info["asset_id"], {}), **_asset(info)}


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


def record_result(ev: Evidence, tool: str, arguments: dict[str, Any], result: dict[str, Any]) -> None:
    """Write what one successful tool result plainly shows into the evidence record."""
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
        if asset_id:
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
        ev.recommendations = {k: result[k] for k in RECOMMENDATION_FIELDS if k in result}
        if ev.priority is None and result.get("priority"):
            ev.priority = {**result["priority"], "alarm_id": result.get("alarm_id", arguments.get("alarm_id"))}
        context = result.get("asset_context")
        _know(ev, context)
        _focus(ev, (context or {}).get("asset_id"))
```

In `apps/backend/copilot/investigation.py`:

1. Delete the `class Evidence(BaseModel): ...` block and add to the imports:

```python
from copilot.evidence import Evidence, record_result
```

2. In `call_tool`, record a successful result before returning it:

```python
        if record.status == "ok":
            record_result(self.evidence, name, args, record.result or {})
            return record.result
```

3. Playbooks stop copying tool results:

`load_asset` becomes:

```python
    async def load_asset(self, asset_id: str) -> dict | None:
        return await self.tool("get_asset_metadata", "Asset details and related assets", asset_id=asset_id)
```

In `find_top_alarm`, delete the `self.evidence.alarm_summary = {...}` statement (5 lines).

`trend_for` keeps only its `await self.tool("get_alarm_trends", ...)` call (drop `trend = ` and the `if trend:` block).

`recommend` becomes:

```python
    async def recommend(self, alarm_id: str) -> dict | None:
        return await self.tool(
            "get_operator_recommendations", "The Alarm API's recommended actions for this alarm", alarm_id=alarm_id
        )
```

In `active_alarms`, delete `self.evidence.active_alarms = found["alarms"]`. In `highest_priority`, delete `self.evidence.active_alarms = found["alarms"]`. (`active_alarms_of_type` keeps its `self.evidence.active_alarms = alarms`: filtering to the type's assets is its judgement.)

In `alarm_from_context_or_asset`, replace

```python
            detail = await self.tool(
                "get_alarm_details", "Reload the alarm from the previous answer", alarm_id=last["alarm_id"]
            )
            if detail:
                self.evidence.focus_alarm = {k: v for k, v in detail.items() if k not in ("asset", "meta")}
                self.evidence.asset = detail["asset"]
            return self.evidence.focus_alarm
```

with

```python
            await self.tool("get_alarm_details", "Reload the alarm from the previous answer", alarm_id=last["alarm_id"])
            return self.evidence.focus_alarm
```

In `recommendation_check`, delete:

```python
        if self.evidence.asset is None and recs.get("asset_context"):
            self.evidence.asset = recs["asset_context"]
```

In `explain_active`, the advice now names the focus asset before its metadata is loaded, so load the metadata when it has not been loaded for that asset (this keeps its related assets, which choose the other alarm's procedure search). Replace `if self.evidence.asset is None:` with `if not self._metadata_loaded(focus["asset_id"]):`, and add after `_no_procedure`:

```python
    def _metadata_loaded(self, asset_id: str) -> bool:
        return any(
            isinstance(s, ToolCallRecord)
            and s.tool == "get_asset_metadata"
            and s.status == "ok"
            and s.arguments.get("asset_id") == asset_id
            for s in self.steps
        )
```

In `service.py` and `context.py`, import `Evidence` from `copilot.evidence` instead of `copilot.investigation`.

In `apps/backend/copilot/agent.py` `_recipe`, the agent's own advice lookup now fills `ev.recommendations` too, so list the API's actions only when the recipe itself fetched them. Before `await getattr(self.inv, step.name)()` add:

```python
        advice_before = ev.recommendations  # the agent's own advice lookup fills the record too
        ev.focused_asset_ids, ev.asset = [], None  # the recipe investigates its own asset; earlier looks do not blur it
```

and replace

```python
        if ev.recommendations:  # only a recipe sets them, and this is the question's one recipe
```

with

```python
        if ev.recommendations and ev.recommendations is not advice_before:  # the advice this recipe fetched
```

(and add `apps/backend/copilot/agent.py` to this task's commit).

- [ ] **Step 4: Run the tests, the full suite, mypy and the offline set**

Run: `uv run python -m pytest apps/backend/tests/test_evidence.py apps/backend/tests/test_agent.py -p no:cacheprovider`
Expected: all pass.
Run: `uv run python -m pytest -p no:cacheprovider` (output to a file; read the tail)
Expected: all pass (playbook answers are unchanged).
Run: `uv run python -m mypy` and `uv run python -m copilot.evaluate` (offline; skip the latter if its preflight says the services are down)
Expected: no mypy issues; 34/34.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . && uv run ruff format --check .
git add apps/backend/copilot/evidence.py apps/backend/copilot/investigation.py apps/backend/copilot/service.py apps/backend/copilot/context.py apps/backend/copilot/agent.py apps/backend/tests/test_evidence.py apps/backend/tests/test_agent.py
git commit -m "refactor: one evidence record, filled from every tool result" -m "Every successful tool call, from a playbook or the agent, writes what its result plainly shows through evidence.record_result. Playbooks keep only their judgements. The question's asset is set only when the calls focus on one asset, so the agent's own lookups now reach the evidence panel and the chat memory, and a comparison leaves it open." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Nothing found is a fact

**Files:**
- Modify: `apps/backend/copilot/evidence.py` (`NegativeFinding`, `Evidence.negatives`)
- Modify: `apps/backend/copilot/investigation.py` (`_found_nothing`; negative wording; `retrieve`)
- Modify: `apps/backend/copilot/facts.py` (`Fact.negative`; negative facts)
- Modify: `apps/backend/copilot/answer.py` (`_negative`; plain summary)
- Modify: `apps/backend/copilot/agent.py` (empty tool results and empty searches as facts)
- Modify: `apps/backend/copilot/evaluate.py` (four negative expectations move from `gap` to `facts`)
- Test: `apps/backend/tests/test_investigation.py`, `apps/backend/tests/test_answer.py`, `apps/backend/tests/test_agent.py`

**Interfaces:**
- Consumes: `Evidence`, `record_result` (Task 1).
- Produces: `copilot.evidence.NegativeFinding(text: str, step: int, tool: str)`; `Evidence.negatives: list[NegativeFinding]`; `Fact.negative: bool = False`; `Investigation._found_nothing(text: str, tool: str) -> None` (`tool` is the MCP tool, or `"search_documents"`); `agent.MAIN_LISTS`.

- [ ] **Step 1: Write the failing tests**

In `apps/backend/tests/test_investigation.py`, change the four assertions on `run.evidence.missing` that check negative findings:

```python
    assert any("Boiler Feed Pump 999" in n.text for n in run.evidence.negatives)
```

```python
    assert f"The documents have no procedure for {kind} alarms." in [n.text for n in run.evidence.negatives]
```

```python
    assert "The documents have no procedure for fan alarms." in [n.text for n in run.evidence.negatives]
```

```python
    assert "There are 0 active alarms on boiler assets (Boiler B-201)." in [n.text for n in run.evidence.negatives]
```

and append (the file already imports `httpx`, `McpToolClient`, `in_memory_connector`, `make_server`, `simulator_transport`):

```python
async def test_a_failed_summary_is_not_a_finding(retriever):
    """A lookup that failed did not find nothing: it is a failure note, not "There are 0 ... alarms"."""
    healthy = simulator_transport()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/alarms/summary":
            return httpx.Response(503, json={"error": {"code": "unavailable", "message": "down"}})
        return await healthy.handle_async_request(request)

    async with McpToolClient(in_memory_connector(make_server(httpx.MockTransport(handler), max_retries=0))) as mcp:
        await mcp.discover()
        u = understand_with_rules("Why are compressor discharge pressure alarms repeatedly occurring?")
        run = await investigate(u, mcp, retriever, ConversationState(), trace_id="t")
    assert not any("alarms on compressor assets" in n.text for n in run.evidence.negatives)
    assert any("summarize_alarms" in n for n in run.evidence.notes)


async def test_negative_findings_are_tied_to_the_lookup_that_found_nothing(mcp, retriever):
    run = await investigate(
        understand_with_rules("Are any boiler alarms active?"), mcp, retriever, ConversationState(), trace_id="t"
    )
    negative = next(n for n in run.evidence.negatives if n.text.startswith("There are 0 active alarms"))
    step = next(s for s in run.steps if s.step == negative.step)
    assert (negative.tool, step.tool) == ("get_alarms", "get_alarms")
    assert run.evidence.missing == []  # nothing failed: it is a fact, not a gap
```

In `apps/backend/tests/test_answer.py` (add `import re` to the imports):

`test_plain_summary_says_when_nothing_is_active` asserts:

```python
    assert re.fullmatch(r"There are 0 active alarms on Boiler Feed Pump 101 \[T\d+\]\.", answer.summary)
```

`test_plain_summary_says_when_no_procedure_applies` asserts:

```python
    assert re.fullmatch(r"The documents have no procedure for boiler alarms \[T\d+\]\.", answer.summary)
```

`test_weak_documents_are_flagged_when_the_alarm_data_is_unavailable` (the search finds 0 passages) asserts:

```python
    assert any("no relevant passages" in f.statement.lower() for f in answer.facts if f.negative)
```

`test_unrelated_question_admits_there_is_no_evidence` replaces its last line with:

```python
    assert any("no relevant passages" in f.statement.lower() for f in answer.facts if f.negative)
    assert answer.summary.endswith("[T1].")  # the honest "nothing found" is cited
```

`test_no_document_gap_when_no_document_search_ran` replaces its gaps assertion on the active alarms with:

```python
    assert "There are 0 active alarms on Boiler Feed Pump 101." in [f.statement for f in answer.facts]
```

and append:

```python
async def test_a_negative_finding_is_a_cited_fact(mcp, retriever):
    answer = await answer_offline(mcp, retriever, "Are any boiler alarms active?")
    fact = next(f for f in answer.facts if f.negative)
    assert fact.statement == "There are 0 active alarms on boiler assets (Boiler B-201)."
    assert answer.summary == f"There are 0 active alarms on boiler assets (Boiler B-201) [{fact.key}]."
    assert any(s.citations == [fact.key] for s in answer.findings)
    assert answer.gaps == []
```

In `apps/backend/tests/test_agent.py`, change the assertion in the site-spelling test from `not any("no active" in m for m in ev.missing)` to:

```python
    assert ev.active_alarms and not any("0 active" in n.text for n in ev.negatives)
```

and append:

```python
async def test_an_empty_tool_result_is_stated_as_a_count(mcp, retriever):
    result, llm = await run_agent(
        "Any active alarms on Boiler B-201?",
        tool("get_alarms", asset_id="AST-BLR-201", status="active"),
        done("Boiler B-201 has 0 active alarms [T1].", "count"),
        mcp=mcp,
        retriever=retriever,
    )
    fact = result.facts[0]
    assert fact.negative and fact.statement.startswith('get_alarms(asset_id="AST-BLR-201", status="active") found 0 alarms')
    answer = compose_agent_answer("Any active alarms on Boiler B-201?", result, llm)
    assert answer.grounding.summary_source == "llm"  # the 0 is in the evidence


async def test_a_document_search_that_finds_nothing_is_a_cited_fact(mcp, retriever):
    result, llm = await run_agent(
        "Do the documents cover chocolate cake?",
        search("chocolate cake recipe"),
        done("The documents do not cover it [T1].", "cannot_answer"),
        mcp=mcp,
        retriever=retriever,
    )
    assert "[T1] The document search for 'chocolate cake recipe' found 0 relevant passages." in llm.prompts[1]
    assert result.facts[0].negative and result.facts[0].tool == "search_documents"


async def test_a_recipes_negative_finding_can_be_cited(mcp, retriever):
    question = "any boiler alarms active?"
    result, llm = await run_agent(
        question,
        recipe("active_alarms", asset_type="boiler"),
        done("There are 0 active alarms on boiler assets [T1].", "count"),
        mcp=mcp,
        retriever=retriever,
    )
    assert "[T1] There are 0 active alarms on boiler assets (Boiler B-201)." in llm.prompts[1]
    answer = compose_agent_answer(question, result, llm)
    assert answer.grounding.summary_source == "llm" and answer.grounding.unknown_citations == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run python -m pytest apps/backend/tests/test_investigation.py apps/backend/tests/test_answer.py apps/backend/tests/test_agent.py -p no:cacheprovider -k "negative or nothing or failed_summary or empty_tool or finds_nothing or no_procedure or weak_documents or unrelated or no_document_gap or site"`
Expected: FAIL (`Evidence` has no `negatives`; `Fact` has no `negative`; texts still say "no").

- [ ] **Step 3: Implement**

`evidence.py`: add before `class Evidence`:

```python
class NegativeFinding(BaseModel):
    """A lookup that worked and found nothing: a fact like any other, tied to the step that established it."""

    text: str
    step: int
    tool: str  # the MCP tool, or "search_documents" for a document search
```

and in `Evidence`, after `retrieval_confidence`:

```python
    negatives: list[NegativeFinding] = []  # lookups that found nothing: facts, not gaps
```

`investigation.py`: import `NegativeFinding` from `copilot.evidence`, and add after `_gap`:

```python
    def _found_nothing(self, text: str, tool: str) -> None:
        """A lookup that worked and found nothing: a fact tied to the step that established it (the latest
        successful step of `tool`), not a gap. Without such a step it was not established, so it stays a gap."""
        step = next(
            (
                s
                for s in reversed(self.steps)
                if s.status == "ok" and (s.tool if isinstance(s, ToolCallRecord) else "search_documents") == tool
            ),
            None,
        )
        if step is None:
            self._gap(text)
        elif all(n.text != text for n in self.evidence.negatives):
            self.evidence.negatives.append(NegativeFinding(text=text, step=step.step, tool=tool))
```

Replace each negative finding (old -> new):

- `resolve_asset`: `self.evidence.missing.append(f"No asset matches '{u.asset_query}'.{closest}")` -> `self._found_nothing(f"No asset matches '{u.asset_query}'.{closest}", "search_assets")`
- `assets_of_type`: `self.evidence.missing.append(f"No {u.asset_type} assets found.")` -> `self._found_nothing(f"No {u.asset_type} assets found.", "search_assets")`
- `pick_asset_by_topic`: after the `summary = await self.tool("summarize_alarms", ...)` call add `if summary is None:` / `return None` (a failed summary found nothing out), change `groups = (summary or {}).get("groups", [])` to `groups = summary.get("groups", [])`, and replace the append with `self._found_nothing(f"There are 0{topic} alarms on {u.asset_type} assets in the last {self.lookback} days.", "summarize_alarms")`
- `find_top_alarm`: `self.evidence.missing.append(f"No {level}alarms on {asset['name']} in the last {self.lookback} days.")` -> `self._found_nothing(f"There are 0 {level}alarms on {asset['name']} in the last {self.lookback} days.", "summarize_alarms")`
- `contributing_factors`: the append of "No alarm consistently preceded the main alarm (no clear contributing factor)." -> `self._found_nothing("No alarm consistently preceded the main alarm (no clear contributing factor).", "correlate_alarms")`
- `active_alarms`: `self.evidence.missing.append(f"There are no active {level}alarms on {where}.")` -> `self._found_nothing(f"There are 0 active {level}alarms on {where}.", "get_alarms")`
- `active_alarms_of_type`: `self.evidence.missing.append(f"There are no active {level}alarms on {kind} assets ({names}).")` -> `self._found_nothing(f"There are 0 active {level}alarms on {kind} assets ({names}).", "get_alarms")`
- `highest_priority`: `self.evidence.missing.append(f"There are no active alarms in {where}.")` -> `self._found_nothing(f"There are 0 active alarms in {where}.", "get_alarms")`

The two "say why" checks now count negative findings too: in `active_alarms` and in `alarm_from_context_or_asset`, `if not self.evidence.missing:` -> `if not (self.evidence.missing or self.evidence.negatives):`.

In `retrieve`, delete

```python
        if not result.passages and no_documents:
            self._gap(no_documents)
        elif main and result.confidence in ("low", "none"):
            self._gap(NO_PASSAGES)
```

and after `await self._record(RetrievalRecord(status="ok", ...))` (before `return kept`) add:

```python
        if not result.passages and (no_documents or main):  # the search ran and found nothing: a fact
            self._found_nothing(no_documents or NO_PASSAGES, "search_documents")
        elif main and result.confidence in ("low", "none"):  # weak passages: a warning, not a finding
            self._gap(NO_PASSAGES)
```

`facts.py`: in `Fact` add `negative: bool = False  # a lookup that found nothing`, and before `return facts` in `build_facts`:

```python
    steps = {s.step for s in run.steps}
    for n in ev.negatives:  # lookups that found nothing, in the order they ran
        if n.step in steps:
            facts.append(Fact(key=f"T{len(facts) + 1}", step=n.step, tool=n.tool, statement=n.text, negative=True))
```

`answer.py`: replace `_no_document_gap` with

```python
def _negative(by_tool: dict[str, list[Fact]], tool: str | None = None) -> Fact | None:
    """The first lookup (of `tool`, when given) that found nothing."""
    found = [f for name, fs in by_tool.items() if tool in (None, name) for f in fs if f.negative]
    return min(found, key=lambda f: int(f.key[1:]), default=None)
```

In `_plain_summary`, replace

```python
    elif ev.intent == "procedure_lookup" and (gap := _no_document_gap(ev.missing)):
        parts.append(gap)
```

with

```python
    elif ev.intent == "procedure_lookup" and (none := _negative(by_tool, "search_documents")):
        parts.append(_cited(none.statement, none.key))
```

and the fallback

```python
    if not parts:
        if ev.missing:
            parts.append(ev.missing[0])  # what the investigation could not find answers the question honestly
```

with

```python
    if not parts:
        if none := _negative(by_tool):  # a lookup that found nothing answers the question honestly, cited
            parts.append(_cited(none.statement, none.key))
        elif ev.missing:
            parts.append(ev.missing[0])
```

`agent.py`: add after `PASSAGE_CHARS`:

```python
MAIN_LISTS = {  # the list a tool's result is about: empty means the lookup found nothing
    "search_assets": ("results", "assets"),
    "get_alarms": ("alarms", "alarms"),
    "summarize_alarms": ("groups", "alarm groups"),
    "get_alarm_trends": ("series", "trend points"),
    "correlate_alarms": ("pairs", "alarm pairs"),
    "analyze_alarm_floods": ("flood_windows", "alarm floods"),
    "find_rationalization_candidates": ("candidates", "candidates"),
}
```

In `_tool`, replace the statement and the fact with:

```python
        field, noun = MAIN_LISTS.get(step.name, ("", ""))
        empty = bool(field) and result.get(field) == []
        verb = f"found 0 {noun}:" if empty else "returned"
        statement = f"{step.name}({_inputs(args)}) {verb} {result_text(result)}"
        fact = Fact(
            key=f"T{len(self.facts) + 1}", step=record.step, tool=step.name, statement=statement, negative=empty
        )
```

In `_search`, replace `return "\n".join(lines) or "No relevant passages."` with:

```python
        if lines:
            return "\n".join(lines)
        text = f"The document search for '{query}' found 0 relevant passages."
        fact = Fact(
            key=f"T{len(self.facts) + 1}", step=record.step, tool="search_documents", statement=text, negative=True
        )
        self.facts.append(fact)
        return f"[{fact.key}] {text}"
```

`evaluate.py` (`GOLDEN`), the negative expectations move from gaps to facts:

- Boiler B-201: `gap="no procedure for boiler alarms", facts=("24",)` -> `facts=("24", "no procedure for boiler alarms")`
- Forced Draft Fan F-201: `gap="no procedure for fan alarms")` -> `facts=("no procedure for fan alarms",))`
- Boiler Feed Pump 999: `gap="No asset matches")` -> `facts=("No asset matches",))`
- "any boiler alarma are active": `gap="no active alarms on boiler assets")` -> `facts=("0 active alarms on boiler assets",))`

- [ ] **Step 4: Run the tests, the full suite, mypy and the offline set**

Run: `uv run python -m pytest apps/backend/tests/test_investigation.py apps/backend/tests/test_answer.py apps/backend/tests/test_agent.py apps/backend/tests/test_evaluate.py -p no:cacheprovider`
Expected: all pass.
Run: `uv run python -m pytest -p no:cacheprovider`, `uv run python -m mypy`, `uv run python -m copilot.evaluate`
Expected: all pass; no mypy issues; 34/34.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . && uv run ruff format --check .
git add apps/backend/copilot/evidence.py apps/backend/copilot/investigation.py apps/backend/copilot/facts.py apps/backend/copilot/answer.py apps/backend/copilot/agent.py apps/backend/copilot/evaluate.py apps/backend/tests/test_investigation.py apps/backend/tests/test_answer.py apps/backend/tests/test_agent.py
git commit -m "feat: a lookup that finds nothing is a cited fact, not a gap" -m "Playbook findings, empty tool results and empty document searches are facts tied to the step that established them, worded with the count (0 active alarms), so an honest negative answer can be cited. Gaps are only what could not be looked up or failed." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: No evidence, no claim

**Files:**
- Modify: `apps/backend/copilot/agent.py` (`AGENT_SYSTEM`, `_looked_up`, `_can_look_up`, `_not_done_yet`, `compose_agent_answer`)
- Modify: `apps/backend/copilot/answer.py` (`compose_answer(answer_type=...)`, `_apply_reply`, `_text_problem`)
- Test: `apps/backend/tests/test_agent.py`

**Interfaces:**
- Consumes: negative facts (Task 2), so an honest negative answer has evidence.
- Produces: `compose_answer(..., answer_type: str | None = None)`; the rejection reason `"no evidence was looked up"`; the refusal text `"nothing has been looked up yet: search the documents or call a tool, or answer cannot_answer."`.

- [ ] **Step 1: Write the failing tests** (append to `test_agent.py`)

```python
async def test_an_answer_needs_something_looked_up_first(mcp, retriever):
    question = "When an AI recommendation disagrees with the operating procedure, which one wins?"
    result, llm = await run_agent(
        question,
        done("The approved procedure always governs.", "document"),
        search("recommendation conflicts with an approved operating procedure, which one governs"),
        done("The approved operating procedure governs [S1].", "document"),
        mcp=mcp,
        retriever=retriever,
    )
    assert "nothing has been looked up yet" in llm.prompts[1]
    assert result.final.direct_answer == "The approved operating procedure governs [S1]."


async def test_a_claim_without_evidence_is_not_shown(mcp, retriever):
    question = "When an AI recommendation disagrees with the operating procedure, which one wins?"
    result, llm = await run_agent(
        question,
        done("The approved procedure always governs.", "document"),
        done("The approved procedure always governs.", "document"),  # asked once to look up; it did not
        mcp=mcp,
        retriever=retriever,
    )
    answer = compose_agent_answer(question, result, llm)
    assert answer.grounding.summary_source == "template"
    assert answer.grounding.summary_rejected == "no evidence was looked up"
    assert answer.answer_type is None


async def test_cannot_answer_needs_no_evidence(mcp, retriever):
    result, llm = await run_agent(
        "What is the capital of France?",
        done("I cannot answer general questions; I help with plant alarms.", "cannot_answer"),
        mcp=mcp,
        retriever=retriever,
    )
    assert len(llm.prompts) == 1  # accepted at once
    answer = compose_agent_answer("What is the capital of France?", result, llm)
    assert answer.summary == "I cannot answer general questions; I help with plant alarms."
    assert answer.answer_type == "cannot_answer"


def test_the_instructions_say_an_answer_rests_on_results():
    assert "with no results, the only answer is cannot_answer" in AGENT_SYSTEM
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run python -m pytest apps/backend/tests/test_agent.py -p no:cacheprovider -k "looked_up_first or without_evidence or needs_no_evidence or rests_on_results"`
Expected: FAIL (the first "done" is accepted; the uncited claim is shown).

- [ ] **Step 3: Implement**

`agent.py`, in `AGENT_SYSTEM` after `- Use only numbers, codes and names that appear in the results. Never invent data.` add:

```text
- Every answer rests on results: look something up first; with no results, the only answer is cannot_answer.
```

In `Agent.__init__` add `self.asked_to_look_up = False`. Add after `_needs_data`:

```python
    def _looked_up(self) -> bool:
        """A lookup has run: a tool call that reached the server, or a document search."""
        return any(isinstance(s, RetrievalRecord) or s.error_code != "INVALID_INPUT" for s in self.inv.steps)

    def _can_look_up(self) -> bool:
        return self.inv.retriever is not None or (bool(self.tools) and not self.inv.mcp_down)
```

and in `_not_done_yet`, after the named-asset rule:

```python
        if (
            step.final.answer_type != "cannot_answer"
            and not self._looked_up()
            and self._can_look_up()
            and not self.asked_to_look_up
        ):
            self.asked_to_look_up = True  # once: the answer checks reject a claim that still rests on nothing
            return "nothing has been looked up yet: search the documents or call a tool, or answer cannot_answer."
```

In `compose_agent_answer`, pass `answer_type=final.answer_type if final else None,` to `compose_answer`.

`answer.py`: `compose_answer` gains the keyword parameter `answer_type: str | None = None` (document it: the agent's answer type; `cannot_answer` may rest on no evidence), and passes `allow_no_evidence=answer_type == "cannot_answer"` to `_apply_reply`. `_apply_reply` gains `allow_no_evidence: bool = False` and passes it to both `_text_problem` calls. `_text_problem` gains `allow_no_evidence: bool = False` and checks first:

```python
    if not valid and not allow_no_evidence:  # nothing was looked up: a claim would rest on the model's memory
        return "no evidence was looked up"
```

- [ ] **Step 4: Run the tests, the full suite and mypy**

Run: `uv run python -m pytest apps/backend/tests/test_agent.py apps/backend/tests/test_answer.py -p no:cacheprovider`
Expected: all pass (including the prompt-size guard).
Run: `uv run python -m pytest -p no:cacheprovider` and `uv run python -m mypy`
Expected: all pass; no issues.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . && uv run ruff format --check .
git add apps/backend/copilot/agent.py apps/backend/copilot/answer.py apps/backend/tests/test_agent.py
git commit -m "feat: no evidence, no claim" -m "The agent is asked once to look something up before answering anything but cannot_answer, and the answer checks reject a claim that rests on nothing. An honest 'I cannot answer that' needs no key." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The exam judges outcomes

**Files:**
- Modify: `apps/backend/copilot/evaluate.py` (`check`, `GOLDEN`, `write_report`)
- Test: `apps/backend/tests/test_evaluate.py`

**Interfaces:**
- Consumes: `understanding.method` (`"rules"` on the rules path, `"llm"` for the agent).

- [ ] **Step 1: Write the failing tests**

In `test_evaluate.py`, delete `as_agent` and `test_a_question_without_data_may_be_answered_by_the_agent`, keep `intent_problems`, and add:

```python
def answered_by_the_agent(response: ChatResponse, intent: str) -> ChatResponse:
    """The same response as if the agent had answered by another route."""
    u = response.understanding.model_copy(update={"intent": intent, "method": "llm"})
    return response.model_copy(update={"understanding": u})


async def test_with_a_model_the_route_is_not_checked(service):
    response = (await evaluate(service, GOLDEN[:1])).rows[0].response
    assert intent_problems(GOLDEN[0], answered_by_the_agent(response, "active_alarms")) == []


async def test_on_the_rules_path_the_route_is_checked(service):
    response = (await evaluate(service, GOLDEN[:1])).rows[0].response
    wrong = response.model_copy(
        update={"understanding": response.understanding.model_copy(update={"intent": "active_alarms"})}
    )
    assert intent_problems(GOLDEN[0], wrong) != []


async def test_the_report_lists_each_answers_document_searches(service, tmp_path):
    report = await evaluate(service, GOLDEN[:1])
    write_report(report, tmp_path / "run.md", "offline")
    text = (tmp_path / "run.md").read_text(encoding="utf-8")
    assert "Searches: pump High Vibration (PMP-VIB-HI)" in text
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run python -m pytest apps/backend/tests/test_evaluate.py -p no:cacheprovider -k "route or searches"`
Expected: FAIL (the agent's other route is reported as an intent problem; no "Searches:" line). If the acceptance run's first search query differs from `pump High Vibration (PMP-VIB-HI)`, use the query of its first `RetrievalRecord` (print it once) and keep the test's intent.

- [ ] **Step 3: Implement** (`evaluate.py`)

In `check`, replace the block from `used = any(...)` through the intent `problems.append(...)` with:

```python
    used = any(isinstance(step, ToolCallRecord) for step in response.trace)
    # The route is what the keyword rules decide, so it is checked on the rules path. With a model the agent may
    # take any route; only what the operator gets is checked.
    if response.understanding.method == "rules" and response.understanding.intent not in intents:
        problems.append(f"intent: {response.understanding.intent}, expected {' or '.join(intents)}")
```

In `GOLDEN`, the Boiler B-201 case asks for a count, not a procedure: `facts=("24", "no procedure for boiler alarms")` -> `facts=("24",)`.

In `write_report`, after the `Tools:` line add the document searches (import `RetrievalRecord` from `copilot.trace` next to `ToolCallRecord`):

```python
        searches = [s.query for s in r.response.trace if isinstance(s, RetrievalRecord)]
        lines += [f"Searches: {'; '.join(searches) or 'none'}", ""]
```

(place it right after `lines += [f"Tools: {', '.join(tools) or 'none'}", ""]`).

- [ ] **Step 4: Run the tests, the CLI and the full suite**

Run: `uv run python -m pytest apps/backend/tests/test_evaluate.py -p no:cacheprovider`
Expected: all pass.
Run: `uv run python -m copilot.evaluate` (offline) and `uv run python -m pytest -p no:cacheprovider` and `uv run python -m mypy`
Expected: 34/34; all pass; no issues.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . && uv run ruff format --check .
git add apps/backend/copilot/evaluate.py apps/backend/tests/test_evaluate.py
git commit -m "test: the exam judges what the operator gets, not which route the agent took" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Documentation, verification, the run with the model

**Files:**
- Modify: `docs/architecture.md`, `docs/design-decisions.md`, `docs/known-limitations.md`, `README.md`, `docs/coverage.md`, `docs/specs/2026-10-13-evidence-integrity-design.md`

- [ ] **Step 1: Update the docs**

- `docs/architecture.md`: the evidence record (`evidence.py`: one recorder at `call_tool`, the table of what each tool records, the one-asset rule); negative facts (playbooks, agent tool results, document searches) and what gaps now are; the "no evidence, no claim" rule in the agent's situation table and in the answer checks.
- `docs/design-decisions.md`: section 20, "Evidence integrity": the three root causes, the three rules, the options considered for the evidence record (A: one recorder, chosen; B: rebuild from the trace, too large a rewrite; C: an agent-only adapter, two places to keep in sync), and the cost (negative findings are now facts, so their wording uses the number 0).
- `docs/known-limitations.md`: agent tool facts still show shortened raw data in the findings; the exam's route is checked only offline.
- `README.md` and `docs/coverage.md`: test counts and coverage from the final run.
- The spec: `Status: built on 2026-10-13.`

- [ ] **Step 2: Full verification**

```bash
uv run python -m pytest -p no:cacheprovider
uv run ruff check .
uv run ruff format --check .
uv run python -m mypy
npm --prefix apps/frontend run test
npm --prefix apps/frontend run typecheck
uv run python -m copilot.evaluate
```

Each must pass; the offline evaluation must report 34/34.

- [ ] **Step 3: Commit the docs**

```bash
git add README.md docs/architecture.md docs/design-decisions.md docs/known-limitations.md docs/coverage.md docs/specs/2026-10-13-evidence-integrity-design.md
git commit -m "docs: evidence integrity (one record, negative facts, no claim without evidence, outcome-based exam)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 4: Run the 38 questions with the model** (the user approved running the tests; check the services first with the offline run)

```bash
uv run python -m copilot.evaluate --llm --skip 0 --limit 12 --report docs/evaluation/2026-10-13-integrity-part1.md
uv run python -m copilot.evaluate --llm --skip 12 --limit 12 --report docs/evaluation/2026-10-13-integrity-part2.md
uv run python -m copilot.evaluate --llm --skip 24 --limit 14 --report docs/evaluation/2026-10-13-integrity-part3.md
```

Read every answer in the reports, list each failure with its cause for the user, then commit the reports.
