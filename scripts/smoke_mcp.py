"""Smoke-test a running Alarm MCP server over streamable HTTP.

    uv run python scripts/smoke_mcp.py

The token comes from MCP_AUTH_TOKEN (environment or .env, the same setting the server uses; Docker without a
.env uses local-mcp-token) and the address from MCP_SERVER_URL (default http://localhost:9000/mcp).
Lists the tools, then runs a short chain: search -> metadata -> correlation, printing the trace.
"""

import asyncio
import json
import os
import sys

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from alarm_mcp.config import McpSettings


async def main() -> int:
    url = os.environ.get("MCP_SERVER_URL", "http://localhost:9000/mcp")
    secret = McpSettings().auth_token
    token = secret.get_secret_value() if secret else None
    if not token:
        print("Set MCP_AUTH_TOKEN (environment or .env)", file=sys.stderr)
        return 2

    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=httpx2.Timeout(30, read=300))
    async with http, Client(streamable_http_client(url, http_client=http)) as client:
        tools = (await client.list_tools()).tools
        print(f"Discovered {len(tools)} tools: {', '.join(t.name for t in tools)}\n")

        trace = {"trace_id": "trace-smoke-001"}
        search = await client.call_tool("search_assets", {"query": "Boiler Feed Pump 101"}, meta=trace)
        asset = search.structured_content["results"][0]
        print(f"1. search_assets        -> {asset['asset_id']} ({asset['name']})")

        meta = await client.call_tool("get_asset_metadata", {"asset_id": asset["asset_id"]}, meta=trace)
        related = [a["asset_id"] for a in meta.structured_content["related_assets"]]
        print(f"2. get_asset_metadata   -> related: {', '.join(related)}")

        args = {"asset_ids": [asset["asset_id"], *related], "lookback_days": 90, "min_support": 3}
        corr = await client.call_tool("correlate_alarms", args, meta=trace)
        print(f"3. correlate_alarms     -> {corr.structured_content['pairs'][0]['insight']}")
        print("\nTrace of step 3:", json.dumps(corr.structured_content["meta"], indent=2))

        missing = await client.call_tool("get_asset_metadata", {"asset_id": "AST-NOPE"}, meta=trace)
        print(f"\nError path -> is_error={missing.is_error}: {missing.content[0].text}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
