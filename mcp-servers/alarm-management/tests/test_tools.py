"""MCP server tool tests over the real MCP protocol (in-memory client)."""

import logging

import httpx
from mcp import Client

from mcp_test_helpers import API_TOKEN, make_server

EXPECTED_TOOLS = {
    "search_assets",
    "get_asset_metadata",
    "get_alarms",
    "get_alarm_details",
    "summarize_alarms",
    "get_alarm_trends",
    "correlate_alarms",
    "analyze_alarm_floods",
    "find_rationalization_candidates",
    "score_alarm_priority",
    "get_operator_recommendations",
    "calculate_kpi",
    "list_kpi_definitions",
}


def error_text(result) -> str:
    assert result.is_error, f"expected an error, got {result.structured_content}"
    return result.content[0].text


# ------------------------------------------------------------------ discovery


async def test_all_tools_are_discoverable_with_schemas(mcp_client):
    tools = {t.name: t for t in (await mcp_client.list_tools()).tools}
    assert set(tools) == EXPECTED_TOOLS
    for tool in tools.values():
        assert tool.description and len(tool.description) > 40, tool.name
        assert tool.input_schema["type"] == "object"
        assert tool.output_schema is not None, f"{tool.name} has no output schema"
        assert tool.annotations.read_only_hint is True


async def test_input_schema_exposes_constraints(mcp_client):
    tools = {t.name: t for t in (await mcp_client.list_tools()).tools}
    limit = tools["search_assets"].input_schema["properties"]["limit"]
    assert limit["minimum"] == 1 and limit["maximum"] == 50
    assert "query" in tools["search_assets"].input_schema["required"]


# ------------------------------------------------------------------ happy path


async def test_search_assets_returns_typed_result_and_trace(mcp_client):
    result = await mcp_client.call_tool("search_assets", {"query": "Boiler Feed Pump 101"})
    assert not result.is_error
    data = result.structured_content
    assert data["results"][0]["asset_id"] == "AST-BFP-101"
    call = data["meta"]["api_calls"][0]
    assert (call["method"], call["path"], call["status_code"], call["outcome"]) == ("GET", "/assets/search", 200, "ok")
    assert data["meta"]["trace_id"].startswith("trace-")


async def test_trace_id_from_request_meta_reaches_the_api(mcp_client, api_transport):
    result = await mcp_client.call_tool("search_assets", {"query": "pump"}, meta={"trace_id": "trace-from-copilot"})
    assert result.structured_content["meta"]["trace_id"] == "trace-from-copilot"
    assert api_transport.requests[-1].headers["x-trace-id"] == "trace-from-copilot"


async def test_chain_search_then_metadata_then_correlation(mcp_client):
    """Outputs of one tool feed the next - the core multi-step pattern."""
    search = await mcp_client.call_tool("search_assets", {"query": "Boiler Feed Pump 101"})
    asset_id = search.structured_content["results"][0]["asset_id"]

    meta = await mcp_client.call_tool("get_asset_metadata", {"asset_id": asset_id})
    related = [a["asset_id"] for a in meta.structured_content["related_assets"]]

    corr = await mcp_client.call_tool(
        "correlate_alarms", {"asset_ids": [asset_id, *related], "lookback_days": 90, "min_support": 3}
    )
    top = corr.structured_content["pairs"][0]
    assert top["leading"]["asset_id"] == "AST-MTR-101"
    assert top["following"]["alarm_code"] == "PMP-VIB-HI"


async def test_lookback_days_is_relative_to_the_api_clock(mcp_client):
    """The data ends 2026-07-01; 'last 90 days' must use the API's clock, not today's date."""
    result = await mcp_client.call_tool("summarize_alarms", {"asset_ids": ["AST-BFP-101"], "lookback_days": 90})
    time_range = result.structured_content["time_range"]
    assert time_range["end_time"].startswith("2026-07-01")
    assert time_range["start_time"].startswith("2026-04-02")
    assert result.structured_content["totals"]["alarm_count"] > 20


async def test_get_alarms_fetch_all_follows_pagination(mcp_client):
    args = {"asset_id": "AST-BFP-101", "page_size": 10, "fetch_all": True}
    data = (await mcp_client.call_tool("get_alarms", args)).structured_content
    assert data["pages_fetched"] > 1
    assert len(data["alarms"]) == data["total_items"]
    assert len(data["meta"]["api_calls"]) == data["pages_fetched"]


async def test_get_alarms_single_page(mcp_client):
    args = {"site": "EastRefinery", "status": "active", "page_size": 50}
    data = (await mcp_client.call_tool("get_alarms", args)).structured_content
    assert data["alarms"]
    assert all(a["status"] == "active" for a in data["alarms"])
    assert data["truncated"] is False


async def test_priority_and_recommendations(mcp_client):
    alarms = (
        await mcp_client.call_tool("get_alarms", {"asset_id": "AST-CMP-301", "status": "active"})
    ).structured_content
    alarm_id = next(a["alarm_id"] for a in alarms["alarms"] if a["alarm_code"] == "CMP-DISCH-PRESS-HI")

    priority = (await mcp_client.call_tool("score_alarm_priority", {"alarm_id": alarm_id})).structured_content
    assert priority["priority"] == "P1"

    recs = (await mcp_client.call_tool("get_operator_recommendations", {"alarm_id": alarm_id})).structured_content
    assert recs["recommended_actions"]
    assert recs["related_assets"] is not None  # context is on by default for this tool
    assert recs["historical_pattern"]["occurrences_prior_90_days"] > 0


async def test_calculate_kpi_records_generate_and_execute_calls(mcp_client):
    args = {"calculation_type": "nuisance_alarm_score", "unit": "Unit 4", "lookback_days": 60}
    data = (await mcp_client.call_tool("calculate_kpi", args)).structured_content
    paths = [c["path"] for c in data["meta"]["api_calls"]]
    # /health first: lookback_days needs the API's data clock (fetched once per session, then cached)
    assert paths == ["/health", "/calculation-code/generate", "/calculation-code/execute"]
    assert 0 <= data["value"] <= 100


async def test_api_clock_is_fetched_once_per_session(mcp_client):
    await mcp_client.call_tool("summarize_alarms", {"lookback_days": 30})
    second = await mcp_client.call_tool("summarize_alarms", {"lookback_days": 60})
    assert [c["path"] for c in second.structured_content["meta"]["api_calls"]] == ["/alarms/summary"]


async def test_flood_and_trend_tools(mcp_client):
    flood = await mcp_client.call_tool("analyze_alarm_floods", {"unit": "Unit 2", "lookback_days": 90})
    assert flood.structured_content["flood_windows"][0]["initiating_alarm"]["alarm_name"] == "Flame Failure"
    trend_args = {"asset_ids": ["AST-BFP-101"], "alarm_codes": ["PMP-VIB-HI"], "bucket": "weekly", "lookback_days": 90}
    trend = await mcp_client.call_tool("get_alarm_trends", trend_args)
    assert trend.structured_content["trend"]["direction"] == "increasing"


# ------------------------------------------------------------------ errors


async def test_invalid_input_is_rejected_before_calling_the_api(mcp_client, api_transport):
    result = await mcp_client.call_tool("search_assets", {"query": "pump", "limit": 0})
    assert result.is_error
    assert api_transport.requests == []


async def test_missing_required_argument(mcp_client):
    assert (await mcp_client.call_tool("get_asset_metadata", {})).is_error


async def test_conflicting_time_arguments_are_rejected(mcp_client):
    args = {"asset_ids": ["AST-BFP-101"], "lookback_days": 30, "start_time": "2026-05-01T00:00:00Z"}
    assert "[INVALID_INPUT]" in error_text(await mcp_client.call_tool("summarize_alarms", args))


async def test_not_found_is_mapped_with_a_hint(mcp_client):
    text = error_text(await mcp_client.call_tool("get_asset_metadata", {"asset_id": "AST-NOPE-1"}))
    assert "[NOT_FOUND]" in text
    assert "search_assets" in text  # tells the model how to recover
    assert "trace_id=" in text


async def test_api_validation_error_is_mapped():
    problems = {"problems": [{"field": "body.site", "message": "too long"}]}
    body = {"error": {"code": "validation_error", "message": "Request validation failed.", "details": problems}}
    rejecting = httpx.MockTransport(lambda req: httpx.Response(422, json=body))
    async with Client(make_server(rejecting)) as client:
        text = error_text(await client.call_tool("summarize_alarms", {"site": "NorthPlant"}))
    assert "[INVALID_INPUT]" in text
    assert "body.site" in text


async def test_unavailable_api_after_retries():
    always_down = httpx.MockTransport(lambda req: httpx.Response(503, json={"error": {"code": "unavailable"}}))
    async with Client(make_server(always_down, max_retries=2)) as client:
        text = error_text(await client.call_tool("search_assets", {"query": "pump"}))
    assert "[UPSTREAM_UNAVAILABLE]" in text
    assert "3 attempt" in text


async def test_timeout_is_reported_as_unavailable():
    def slow(req):
        raise httpx.ReadTimeout("slow")

    async with Client(make_server(httpx.MockTransport(slow), max_retries=1)) as client:
        text = error_text(await client.call_tool("search_assets", {"query": "pump"}))
    assert "[UPSTREAM_UNAVAILABLE]" in text
    assert "did not respond" in text


async def test_wrong_api_token_is_reported_without_leaking_it(api_transport):
    async with Client(make_server(api_transport, token="definitely-wrong-token")) as client:
        text = error_text(await client.call_tool("search_assets", {"query": "pump"}))
    assert "[AUTH_FAILED]" in text
    assert "definitely-wrong-token" not in text


async def test_malformed_api_response_fails_output_validation():
    bad = httpx.MockTransport(lambda req: httpx.Response(200, json={"query": "x", "total": 1, "results": [{"x": 1}]}))
    async with Client(make_server(bad)) as client:
        assert (await client.call_tool("search_assets", {"query": "pump"})).is_error


async def test_unknown_tool_is_an_error(mcp_client):
    assert (await mcp_client.call_tool("delete_all_alarms", {})).is_error


async def test_api_token_never_appears_in_logs(mcp_client, caplog):
    caplog.set_level(logging.DEBUG)
    await mcp_client.call_tool("search_assets", {"query": "pump"})
    await mcp_client.call_tool("get_asset_metadata", {"asset_id": "AST-NOPE"})
    assert API_TOKEN not in caplog.text
