from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from mcp.server import MCPServer

from alarm_api_client import AlarmApiClient
from alarm_mcp import __version__
from alarm_mcp.runtime import AppContext
from alarm_mcp.tools import register_tools

INSTRUCTIONS = """\
Tools for investigating industrial plant alarms through the Alarm Management API (read-only).
Typical investigation: search_assets (name -> asset_id) -> get_asset_metadata (related assets) ->
get_alarms / summarize_alarms / get_alarm_trends -> correlate_alarms (asset + related assets) ->
score_alarm_priority -> get_operator_recommendations.
Time windows: prefer lookback_days; it is measured from the API's data clock, not today's date.
Errors start with a [CODE] such as [NOT_FOUND] or [UPSTREAM_UNAVAILABLE] and say how to recover.
"""


def build_server(api_client_factory: Callable[[], AlarmApiClient]) -> MCPServer:
    """Create the MCP server. The factory is called once per MCP session (lifespan)."""

    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[AppContext]:
        client = api_client_factory()
        try:
            yield AppContext(client=client)
        finally:
            await client.aclose()

    mcp = MCPServer(
        "alarm-management",
        title="Alarm Management MCP Server",
        version=__version__,
        instructions=INSTRUCTIONS,
        lifespan=lifespan,
    )
    register_tools(mcp)
    return mcp
