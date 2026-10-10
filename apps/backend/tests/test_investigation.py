"""Playbooks: multi-step MCP chains + RAG in one workflow, with partial-failure handling."""

import httpx

from copilot.investigation import ConversationState, investigate
from copilot.mcp_client import McpToolClient, http_connector, in_memory_connector
from copilot.understanding import understand_with_rules
from copilot_test_helpers import simulator_transport
from mcp_test_helpers import make_server

ACCEPTANCE = (
    "Investigate recurring high-severity alarms for Boiler Feed Pump 101 over the last 90 days, identify likely "
    "contributing factors, retrieve the relevant operating procedure, and provide recommended actions."
)


def tools_called(run) -> list[str]:
    return [s.tool for s in run.steps if s.kind == "mcp_tool"]


def labels(run) -> set[str]:
    return {p.citation.label for p in run.evidence.passages}


# -------------------------------------------------------------- acceptance scenario


async def test_acceptance_investigation_chains_mcp_and_rag(mcp, retriever):
    run = await investigate(understand_with_rules(ACCEPTANCE), mcp, retriever, ConversationState(), trace_id="t-acc")
    ev = run.evidence

    assert ev.asset["asset_id"] == "AST-BFP-101"
    assert {"AST-MTR-101", "AST-DEA-101"} <= {a["asset_id"] for a in ev.related_assets}
    assert ev.top_alarm["alarm_code"] == "PMP-VIB-HI"
    assert ev.trend["direction"] == "increasing"
    lead = ev.contributing_factors[0]
    assert (lead["leading"]["asset_id"], lead["leading"]["alarm_code"]) == ("AST-MTR-101", "MTR-BRG-TEMP-HI")
    assert ev.focus_alarm["alarm_code"] == "PMP-VIB-HI"
    assert ev.priority["priority"] in {"P1", "P2"}
    assert ev.recommendations["recommended_actions"]

    assert "SOP-BFP-001 §4.1" in labels(run)
    assert labels(run) & {"MM-PUMP-002 §4", "TG-MTR-003 §5", "KB-008 › LESSONS LEARNED"}
    assert "SAF-005 §2" in labels(run)  # lockout steps before any field inspection

    assert tools_called(run)[:3] == ["search_assets", "get_asset_metadata", "summarize_alarms"]
    assert all(s.status == "ok" for s in run.steps)
    assert [s.step for s in run.steps] == list(range(1, len(run.steps) + 1))


async def test_outputs_feed_later_steps(mcp, retriever):
    run = await investigate(understand_with_rules(ACCEPTANCE), mcp, retriever, ConversationState(), trace_id="t")
    by_tool = {s.tool: s for s in run.steps if s.kind == "mcp_tool"}
    assert by_tool["get_asset_metadata"].arguments["asset_id"] == "AST-BFP-101"  # from search_assets
    assert "AST-MTR-101" in by_tool["correlate_alarms"].arguments["asset_ids"]  # from get_asset_metadata
    assert by_tool["get_alarm_trends"].arguments["alarm_codes"] == ["PMP-VIB-HI"]  # from summarize_alarms
    focus_id = run.evidence.focus_alarm["alarm_id"]
    assert by_tool["get_operator_recommendations"].arguments["alarm_id"] == focus_id  # from get_alarms
    assert by_tool["summarize_alarms"].arguments["lookback_days"] == 90
    assert by_tool["summarize_alarms"].arguments["severity"] == ["high", "critical"]


async def test_retrieval_steps_use_the_same_keys_as_the_answer(mcp, retriever):
    """[S2] in the trace must be the same passage as [S2] in the answer."""
    run = await investigate(understand_with_rules(ACCEPTANCE), mcp, retriever, ConversationState(), trace_id="t")
    by_key = {p.citation.key: p.chunk_id for p in run.evidence.passages}
    refs = [r for s in run.steps if s.kind == "rag_retrieval" for r in s.results]
    assert refs
    for ref in refs:
        if ref.key is not None:
            assert by_key[ref.key] == ref.chunk_id


async def test_steps_are_reported_as_they_happen(mcp, retriever):
    seen = []

    async def on_step(record):
        seen.append((record.step, record.kind))

    run = await investigate(
        understand_with_rules(ACCEPTANCE), mcp, retriever, ConversationState(), trace_id="t", on_step=on_step
    )
    assert seen == [(s.step, s.kind) for s in run.steps]


async def test_trace_id_is_shared_by_every_step(mcp, retriever):
    run = await investigate(understand_with_rules(ACCEPTANCE), mcp, retriever, ConversationState(), trace_id="t-share")
    assert {s.trace_id for s in run.steps} == {"t-share"}


# ------------------------------------------------------------------- other intents


async def test_topic_without_asset_picks_the_worst_compressor(mcp, retriever):
    u = understand_with_rules("Why are compressor discharge pressure alarms repeatedly occurring?")
    ev = (await investigate(u, mcp, retriever, ConversationState(), trace_id="t")).evidence
    assert ev.asset["asset_id"] == "AST-CMP-301"
    assert ev.top_alarm["alarm_code"] == "CMP-DISCH-PRESS-HI"
    assert any(f["leading"]["alarm_code"] == "HX-OUT-TEMP-HI" for f in ev.contributing_factors)
    assert any("Selected" in n for n in ev.notes)


async def test_active_alarms_are_ranked_and_explained(mcp, retriever):
    u = understand_with_rules("Show active critical alarms for Boiler Feed Pump 102 and recommend immediate actions.")
    run = await investigate(u, mcp, retriever, ConversationState(), trace_id="t")
    ev = run.evidence
    assert ev.active_alarms and all(a["status"] == "active" and a["severity"] == "critical" for a in ev.active_alarms)
    scores = [r["score"] for r in ev.ranked_alarms]
    assert scores == sorted(scores, reverse=True)
    assert ev.focus_alarm["alarm_id"] == ev.ranked_alarms[0]["alarm_id"]
    assert ev.recommendations is not None
    assert any(label.startswith("SOP-BFP-001") for label in labels(run))


async def test_highest_priority_in_site(mcp, retriever):
    u = understand_with_rules("Which alarm has the highest priority in EastRefinery, and why?")
    run = await investigate(u, mcp, retriever, ConversationState(), trace_id="t")
    ev = run.evidence
    assert ev.focus_alarm["asset_id"] == "AST-CMP-301"
    assert len(ev.ranked_alarms) >= 5
    assert ev.priority["factors"]
    assert "ALM-PHIL-006 §2" in labels(run)


async def test_related_assets_for_motor_trip(mcp, retriever):
    u = understand_with_rules("What related assets should be inspected for this motor trip alarm?")
    run = await investigate(u, mcp, retriever, ConversationState(), trace_id="t")
    ev = run.evidence
    assert ev.asset["asset_id"] == "AST-MTR-501"
    assert {"AST-CMP-502", "AST-MTR-502"} <= {a["asset_id"] for a in ev.related_assets}
    assert ev.top_alarm["alarm_code"] == "MTR-TRIP"
    assert {f["leading"]["alarm_code"] for f in ev.contributing_factors} & {"CMP-SURGE", "MTR-WIND-TEMP-HI"}
    assert "TG-MTR-003 §6" in labels(run)


async def test_follow_up_procedure_uses_conversation_context(mcp, retriever):
    state = ConversationState()
    await investigate(understand_with_rules(ACCEPTANCE), mcp, retriever, state, trace_id="t1")
    assert state.last_alarm["alarm_code"] == "PMP-VIB-HI"

    run = await investigate(
        understand_with_rules("Which operating procedure applies to this alarm?"), mcp, retriever, state, trace_id="t2"
    )
    assert tools_called(run)[0] == "get_alarm_details"
    assert run.steps[0].arguments["alarm_id"] == state.last_alarm["alarm_id"]
    assert "SOP-BFP-001 §4.1" in labels(run)


async def test_recommendation_check_retrieves_guidance_per_action(mcp, retriever):
    state = ConversationState()
    await investigate(understand_with_rules(ACCEPTANCE), mcp, retriever, state, trace_id="t1")
    u = understand_with_rules("Are the API recommendations consistent with the maintenance manual?")
    run = await investigate(u, mcp, retriever, state, trace_id="t2")
    assert "get_operator_recommendations" in tools_called(run)
    retrievals = [s for s in run.steps if s.kind == "rag_retrieval"]
    assert len(retrievals) >= len(run.evidence.recommendations["recommended_actions"])
    assert "SOP-BFP-001 §4.1" in labels(run)


async def test_knowledge_question_uses_only_rag(mcp, retriever):
    u = understand_with_rules("What does the safety instruction say about lockout?")
    run = await investigate(u, mcp, retriever, ConversationState(), trace_id="t")
    assert tools_called(run) == []
    assert "SAF-005 §2" in labels(run)


# ---------------------------------------------------------------- failure handling


async def test_unknown_asset_stops_asset_steps(mcp, retriever):
    u = understand_with_rules("Investigate Boiler Feed Pump 999")
    run = await investigate(u, mcp, retriever, ConversationState(), trace_id="t")
    assert tools_called(run) == ["search_assets"]
    assert any("Boiler Feed Pump 999" in m for m in run.evidence.missing)


async def test_partial_failure_keeps_the_rest_of_the_investigation(retriever):
    """Correlation endpoint is down; everything else still works."""
    healthy = simulator_transport()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/alarms/correlation":
            return httpx.Response(503, json={"error": {"code": "unavailable", "message": "maintenance"}})
        return await healthy.handle_async_request(request)

    async with McpToolClient(in_memory_connector(make_server(httpx.MockTransport(handler), max_retries=1))) as mcp:
        await mcp.discover()
        run = await investigate(understand_with_rules(ACCEPTANCE), mcp, retriever, ConversationState(), trace_id="t")

    failed = [s for s in run.steps if s.status != "ok"]
    assert [s.tool for s in failed] == ["correlate_alarms"]
    assert failed[0].error_code == "UPSTREAM_UNAVAILABLE"
    assert run.evidence.contributing_factors == []
    assert any("correlate_alarms" in n for n in run.evidence.notes)
    assert run.evidence.recommendations is not None  # later steps still ran
    assert "SOP-BFP-001 §4.1" in labels(run)


async def test_mcp_server_down_still_returns_document_evidence(retriever):
    async with McpToolClient(http_connector("http://127.0.0.1:9/mcp", token="x", timeout_seconds=2)) as mcp:
        run = await investigate(understand_with_rules(ACCEPTANCE), mcp, retriever, ConversationState(), trace_id="t")
    assert run.steps[0].error_code == "MCP_UNAVAILABLE"
    assert any("MCP" in n for n in run.evidence.notes)
    assert labels(run)  # RAG still answers from the documents
