"""Per-call plumbing shared by every tool: trace id, API call recording, error mapping, logging."""

import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ValidationError

from alarm_api_client import (
    AlarmApiClient,
    AlarmApiError,
    ApiResponse,
    AuthenticationError,
    NotFoundError,
    PageResult,
    RateLimitedError,
    RequestValidationError,
    UpstreamUnavailableError,
    new_trace_id,
)
from alarm_mcp.schemas import ApiCall, ToolMeta

log = logging.getLogger("alarm_mcp")
_SAFE_TRACE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
M = TypeVar("M", bound=BaseModel)


@dataclass
class AppContext:
    """Created once per MCP session by the server lifespan."""

    client: AlarmApiClient
    reference_now: datetime | None = None  # the API's clock, fetched lazily from /health


def invalid_input(message: str) -> ToolError:
    return ToolError(f"[INVALID_INPUT] {message}")


def _not_found_hint(path: str | None) -> str:
    if path and path.startswith("/assets"):
        return "Use search_assets to find a valid asset_id."
    if path and "calculation" in path:
        return "Run calculate_kpi again to create a new calculation."
    return "Use get_alarms to find a valid alarm_id."


def to_tool_error(err: AlarmApiError, trace_id: str) -> ToolError:
    """Translate connector errors into short, model-readable messages with a stable [CODE] prefix."""
    if isinstance(err, AuthenticationError):
        text = "[AUTH_FAILED] The Alarm API rejected the MCP server's credentials. This is a configuration problem."
    elif isinstance(err, NotFoundError):
        text = f"[NOT_FOUND] {err.message} {_not_found_hint(err.path)}"
    elif isinstance(err, RequestValidationError):
        problems = (err.details or {}).get("problems", []) if isinstance(err.details, dict) else []
        listed = "; ".join(f"{p.get('field')}: {p.get('message')}" for p in problems)
        text = f"[INVALID_INPUT] {err.message}" + (f" Problems: {listed}" if listed else "")
    elif isinstance(err, RateLimitedError):
        text = f"[RATE_LIMITED] The Alarm API is throttling requests; gave up after {err.attempts} attempt(s)."
    elif isinstance(err, UpstreamUnavailableError):
        text = f"[UPSTREAM_UNAVAILABLE] {err.message} Gave up after {err.attempts} attempt(s)."
    else:
        text = f"[API_ERROR] {err.message}"
    return ToolError(f"{text} (trace_id={trace_id})")


class ToolRun:
    """One tool invocation. Records every API call so the result can show a full trace."""

    def __init__(self, app: AppContext, tool: str, meta: dict[str, Any] | None):
        self.app = app
        self.client = app.client
        self.tool = tool
        incoming = (meta or {}).get("trace_id")
        self.trace_id = incoming if isinstance(incoming, str) and _SAFE_TRACE.match(incoming) else new_trace_id()
        self.calls: list[ApiCall] = []
        self._started = time.perf_counter()

    # ------------------------------------------------------------- API calls
    async def api(self, fn: Callable[..., Awaitable[ApiResponse]], *args: Any, **kwargs: Any) -> Any:
        try:
            resp = await fn(*args, trace_id=self.trace_id, **kwargs)
        except AlarmApiError as err:
            self._record_error(err)
            raise to_tool_error(err, self.trace_id) from err
        except ValueError as err:  # connector rejected an id before sending
            raise invalid_input(str(err)) from err
        self._record(resp)
        return resp.data

    async def all_pages(self, **filters: Any) -> PageResult:
        try:
            result = await self.client.list_all_alarms(trace_id=self.trace_id, **filters)
        except AlarmApiError as err:
            self._record_error(err)
            raise to_tool_error(err, self.trace_id) from err
        for resp in result.calls:
            self._record(resp)
        return result

    async def reference_now(self) -> datetime:
        """The API's notion of 'now' (the data is historical), cached for the session."""
        if self.app.reference_now is None:
            health = await self.api(self.client.health)
            self.app.reference_now = datetime.fromisoformat(health["reference_now"])
        return self.app.reference_now

    async def time_range(
        self, start: datetime | None, end: datetime | None, lookback_days: int | None
    ) -> dict[str, str] | None:
        """Resolve explicit times or a lookback relative to the API clock. None = let the API default."""
        if lookback_days is not None and (start or end):
            raise invalid_input("Use either lookback_days or start_time/end_time, not both.")
        if lookback_days is None and not start and not end:
            return None
        start = _as_utc(start) if start else None
        end = _as_utc(end) if end else None
        if lookback_days is not None or end is None:
            end = end or await self.reference_now()
        if lookback_days is not None:
            start = end - timedelta(days=lookback_days)
        start = start or end - timedelta(days=90)
        if start >= end:
            raise invalid_input("start_time must be before end_time.")
        return {"start_time": start.isoformat(), "end_time": end.isoformat()}

    # --------------------------------------------------------------- result
    def meta(self) -> ToolMeta:
        return ToolMeta(trace_id=self.trace_id, api_calls=self.calls)

    def output(self, model: type[M], data: dict[str, Any]) -> M:
        try:
            result = model.model_validate({**data, "meta": self.meta()})
        except ValidationError as err:
            self._log("error", error_code="INVALID_API_RESPONSE")
            raise ToolError(
                f"[INVALID_API_RESPONSE] The Alarm API returned data that does not match the "
                f"{model.__name__} contract ({err.error_count()} problem(s)). (trace_id={self.trace_id})"
            ) from err
        self._log("ok")
        return result

    # -------------------------------------------------------------- private
    def _record(self, resp: ApiResponse) -> None:
        self.calls.append(
            ApiCall(
                method=resp.method,
                path=resp.path,
                status_code=resp.status_code,
                attempts=resp.attempts,
                duration_ms=resp.duration_ms,
                outcome="ok",
            )
        )

    def _record_error(self, err: AlarmApiError) -> None:
        self.calls.append(
            ApiCall(
                method=err.method,
                path=err.path,
                status_code=err.status_code,
                attempts=err.attempts,
                duration_ms=None,
                outcome="error",
                error_code=err.code,
            )
        )
        self._log("error", error_code=type(err).__name__)

    def _log(self, outcome: str, error_code: str | None = None) -> None:
        log.info(
            "tool call",
            extra={
                "fields": {
                    "trace_id": self.trace_id,
                    "mcp_server": "alarm-management",
                    "tool": self.tool,
                    "outcome": outcome,
                    "error_code": error_code,
                    "duration_ms": round((time.perf_counter() - self._started) * 1000, 1),
                    "api_calls": len(self.calls),
                    "retries": sum(c.attempts - 1 for c in self.calls),
                }
            },
        )


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
