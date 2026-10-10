"""MCP client integration: discovery, schema-checked invocation, error mapping, unavailable tools/servers."""

import httpx

from alarm_simulator.config import Settings as SimulatorSettings
from alarm_simulator.main import create_app
from copilot.mcp_client import McpToolClient, http_connector, in_memory_connector
from mcp_test_helpers import API_TOKEN, RecordingTransport, make_server


def simulator_transport() -> RecordingTransport:
    return RecordingTransport(httpx.ASGITransport(app=create_app(SimulatorSettings(api_token=API_TOKEN))))


async def test_discovers_tools_with_schemas():
    async with McpToolClient(in_memory_connector(make_server(simulator_transport()))) as client:
        tools = await client.discover()
    names = {t.name for t in tools}
    assert {"search_assets", "correlate_alarms", "get_operator_recommendations"} <= names
    spec = client.tool("search_assets")
    assert spec.server == "alarm-management"
    assert spec.input_schema["required"] == ["query"]
    assert spec.output_schema is not None


async def test_successful_call_is_recorded_with_api_trace():
    async with McpToolClient(in_memory_connector(make_server(simulator_transport()))) as client:
        await client.discover()
        record = await client.call("search_assets", {"query": "Boiler Feed Pump 101"}, trace_id="trace-t1")
    assert record.status == "ok"
    assert record.result["results"][0]["asset_id"] == "AST-BFP-101"
    assert record.trace_id == "trace-t1"
    assert record.result["meta"]["trace_id"] == "trace-t1"
    assert record.api_calls[0]["path"] == "/assets/search"
    assert record.retries == 0
    assert record.duration_ms > 0


async def test_invalid_arguments_are_caught_before_calling():
    transport = simulator_transport()
    async with McpToolClient(in_memory_connector(make_server(transport))) as client:
        await client.discover()
        record = await client.call("search_assets", {"query": "pump", "limit": 0}, trace_id="t")
    assert record.status == "invalid_input"
    assert "limit" in record.error_message
    assert transport.requests == []  # nothing reached the API


async def test_missing_required_argument():
    async with McpToolClient(in_memory_connector(make_server(simulator_transport()))) as client:
        await client.discover()
        record = await client.call("get_asset_metadata", {}, trace_id="t")
    assert record.status == "invalid_input"
    assert "asset_id" in record.error_message


async def test_unknown_tool_is_reported_unavailable():
    async with McpToolClient(in_memory_connector(make_server(simulator_transport()))) as client:
        await client.discover()
        record = await client.call("delete_everything", {}, trace_id="t")
    assert record.status == "unavailable"
    assert "not offered" in record.error_message


async def test_tool_error_code_is_parsed():
    async with McpToolClient(in_memory_connector(make_server(simulator_transport()))) as client:
        await client.discover()
        record = await client.call("get_asset_metadata", {"asset_id": "AST-NOPE"}, trace_id="t")
    assert record.status == "error"
    assert record.error_code == "NOT_FOUND"
    assert "search_assets" in record.error_message


async def test_upstream_outage_is_reported_with_retries():
    down = httpx.MockTransport(lambda req: httpx.Response(503, json={"error": {"code": "unavailable"}}))
    async with McpToolClient(in_memory_connector(make_server(down, max_retries=2))) as client:
        await client.discover()
        record = await client.call("search_assets", {"query": "pump"}, trace_id="t")
    assert record.status == "error"
    assert record.error_code == "UPSTREAM_UNAVAILABLE"


async def test_unreachable_mcp_server_does_not_raise():
    connector = http_connector("http://127.0.0.1:9/mcp", token="x", timeout_seconds=2)
    async with McpToolClient(connector) as client:
        tools = await client.discover()
        record = await client.call("search_assets", {"query": "pump"}, trace_id="t")
    assert tools == []
    assert client.connected is False
    assert record.status == "unavailable"
    assert record.error_code == "MCP_UNAVAILABLE"
