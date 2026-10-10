"""MCP client used by the orchestrator: tool discovery, schema-checked calls, and failure isolation.

A call never raises. Every outcome - success, tool error, invalid arguments, unknown tool, server
down, timeout - comes back as a ToolCallRecord, so one failed step cannot crash an investigation.
"""

import logging
import re
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import anyio
import httpx2
from jsonschema import Draft202012Validator
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server import MCPServer

from copilot.trace import ToolCallRecord, ToolSpec, ToolStatus

log = logging.getLogger("copilot.mcp")

Connector = Callable[[], AbstractAsyncContextManager[Client]]
_ERROR_CODE = re.compile(r"\[([A-Z_]+)\]")
_SDK_PREFIX = re.compile(r"^Error executing tool \w+: ")


def in_memory_connector(server: MCPServer) -> Connector:
    """Talk to an MCPServer object in-process (tests)."""
    return lambda: Client(server)


def http_connector(url: str, token: str, timeout_seconds: float = 30) -> Connector:
    """Talk to a running MCP server over streamable HTTP with a bearer token."""

    @asynccontextmanager
    async def session() -> AsyncIterator[Client]:
        timeout = httpx2.Timeout(timeout_seconds, read=max(timeout_seconds, 60))
        async with (
            httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=timeout) as http,
            Client(streamable_http_client(url, http_client=http)) as client,
        ):
            yield client

    return session


class McpToolClient:
    """One MCP session (opened per copilot request) plus the tool registry discovered on it."""

    def __init__(self, connector: Connector, server_name: str = "alarm-management", call_timeout_seconds: float = 60):
        self._connector = connector
        self.server_name = server_name
        self.call_timeout_seconds = call_timeout_seconds
        self._stack = AsyncExitStack()
        self._client: Client | None = None
        self._tools: dict[str, ToolSpec] = {}
        self.connected = False
        self.last_error: str | None = None

    async def __aenter__(self) -> "McpToolClient":
        try:
            self._client = await self._stack.enter_async_context(self._connector())
            self.connected = True
        except Exception as exc:  # noqa: BLE001 - any connection failure means "server unavailable"
            self.last_error = f"{type(exc).__name__}: {exc}"[:300]
            log.warning("mcp connect failed", extra={"fields": {"server": self.server_name, "error": self.last_error}})
            await self._stack.aclose()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._stack.aclose()
        self.connected = False

    # ---------------------------------------------------------------- discovery
    async def discover(self) -> list[ToolSpec]:
        if not self.connected or self._client is None:
            return []
        try:
            listed = await self._client.list_tools()
        except Exception as exc:  # noqa: BLE001
            self.connected, self.last_error = False, f"{type(exc).__name__}: {exc}"[:300]
            return []
        self._tools = {
            t.name: ToolSpec(
                server=self.server_name,
                name=t.name,
                title=t.title,
                description=t.description or "",
                input_schema=t.input_schema,
                output_schema=t.output_schema,
                read_only=bool(t.annotations and t.annotations.read_only_hint),
            )
            for t in listed.tools
        }
        return list(self._tools.values())

    def tool(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    @property
    def tools(self) -> list[ToolSpec]:
        return list(self._tools.values())

    # -------------------------------------------------------------------- calls
    async def call(self, tool: str, arguments: dict[str, Any], *, trace_id: str, purpose: str = "") -> ToolCallRecord:
        started, started_at = time.perf_counter(), datetime.now(UTC)

        def record(status: ToolStatus, **fields: Any) -> ToolCallRecord:
            rec = ToolCallRecord(
                server=self.server_name,
                tool=tool,
                purpose=purpose,
                arguments=arguments,
                status=status,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                trace_id=trace_id,
                started_at=started_at,
                **fields,
            )
            log.info(
                "mcp tool call",
                extra={
                    "fields": {
                        "trace_id": trace_id,
                        "mcp_server": self.server_name,
                        "mcp_tool": tool,
                        "outcome": status,
                        "error_code": rec.error_code,
                        "duration_ms": rec.duration_ms,
                        "retries": rec.retries,
                    }
                },
            )
            return rec

        if not self.connected or self._client is None:
            return record(
                "unavailable",
                error_code="MCP_UNAVAILABLE",
                error_message=f"MCP server '{self.server_name}' is not reachable ({self.last_error}).",
            )
        spec = self._tools.get(tool)
        if spec is None:
            return record(
                "unavailable",
                error_code="TOOL_NOT_FOUND",
                error_message=f"Tool '{tool}' is not offered by MCP server '{self.server_name}'.",
            )
        problems = sorted(Draft202012Validator(spec.input_schema).iter_errors(arguments), key=lambda e: e.path)
        if problems:
            text = "; ".join(f"{'.'.join(map(str, e.absolute_path)) or 'arguments'}: {e.message}" for e in problems)
            return record("invalid_input", error_code="INVALID_INPUT", error_message=text)

        try:
            with anyio.fail_after(self.call_timeout_seconds):
                result = await self._client.call_tool(tool, arguments, meta={"trace_id": trace_id})
        except TimeoutError:
            return record(
                "error", error_code="MCP_TIMEOUT", error_message=f"No reply within {self.call_timeout_seconds}s."
            )
        except Exception as exc:  # noqa: BLE001 - transport failure mid-session
            self.connected, self.last_error = False, f"{type(exc).__name__}: {exc}"[:300]
            return record("unavailable", error_code="MCP_UNAVAILABLE", error_message=self.last_error)

        if result.is_error:
            text = " ".join(getattr(c, "text", "") for c in result.content).strip()
            code = _ERROR_CODE.search(text)
            return record(
                "error", error_code=code.group(1) if code else "TOOL_ERROR", error_message=_SDK_PREFIX.sub("", text)
            )

        data = result.structured_content or {}
        api_calls = (data.get("meta") or {}).get("api_calls", [])
        return record("ok", result=data, api_calls=api_calls, retries=sum(c.get("attempts", 1) - 1 for c in api_calls))
