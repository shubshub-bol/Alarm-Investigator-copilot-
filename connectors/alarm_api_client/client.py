"""Async HTTP client for the Alarm Management API.

Handles: bearer auth, trace propagation, timeouts, retry with exponential backoff (honouring
Retry-After), error-envelope mapping to typed errors, pagination, and safe path building.
All analysis POSTs are read-only, so retrying them is safe.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from alarm_api_client.config import ClientSettings
from alarm_api_client.errors import (
    AlarmApiError,
    ApiTimeoutError,
    UpstreamUnavailableError,
    error_for_status,
)

log = logging.getLogger("alarm_api_client")

Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class ApiResponse:
    """Response body plus call metadata (shown in the copilot's tool trace)."""

    data: Any
    status_code: int
    attempts: int
    duration_ms: float
    trace_id: str
    method: str
    path: str


@dataclass
class PageResult:
    items: list[dict] = field(default_factory=list)
    pages_fetched: int = 0
    truncated: bool = False
    total_items: int | None = None
    trace_id: str = ""


def new_trace_id() -> str:
    return f"trace-{uuid.uuid4().hex[:16]}"


def _segment(value: str, name: str) -> str:
    """Validate and URL-escape an id used inside a path, so '../x' cannot change the route."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return quote(value.strip(), safe="")


def _csv(value: str | Sequence[str] | None) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return ",".join(value)


def _iso(value: datetime | str | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else value


class AlarmApiClient:
    def __init__(
        self,
        settings: ClientSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
    ):
        self.settings = settings
        self._sleep = sleep
        self._http = httpx.AsyncClient(
            base_url=settings.base_url,
            timeout=settings.timeout_seconds,
            transport=transport,
            headers={"Accept": "application/json"},
        )

    async def __aenter__(self) -> "AlarmApiClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ core
    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> ApiResponse:
        trace_id = trace_id or new_trace_id()
        headers = {
            "Authorization": f"Bearer {self.settings.token.get_secret_value()}",
            "x-trace-id": trace_id,
            "trace_id": trace_id,  # the header name used by the Postman collections
            "x-client-id": self.settings.client_id,
        }
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        max_attempts = 1 + self.settings.max_retries
        started = time.perf_counter()

        for attempt in range(1, max_attempts + 1):
            retry_after: float | None = None
            try:
                resp = await self._http.request(method, path, params=clean_params, json=json, headers=headers)
            except httpx.TimeoutException:
                failure: AlarmApiError = ApiTimeoutError(
                    f"Alarm API did not respond within {self.settings.timeout_seconds}s."
                )
            except httpx.TransportError as exc:
                failure = UpstreamUnavailableError(f"Could not reach the Alarm API ({type(exc).__name__}).")
            else:
                if resp.status_code < 400:
                    return self._success(resp, method, path, attempt, started, trace_id)
                failure = self._error_from_response(resp)
                retry_after = self._retry_after(resp)

            failure.attempts, failure.method, failure.path = attempt, method, path
            failure.trace_id = failure.trace_id or trace_id
            will_retry = failure.retryable and attempt < max_attempts
            log.warning(
                "alarm api call failed",
                extra={
                    "fields": {
                        "trace_id": trace_id,
                        "method": method,
                        "path": path,
                        "status": failure.status_code,
                        "error_code": failure.code,
                        "attempt": attempt,
                        "will_retry": will_retry,
                    }
                },
            )
            if not will_retry:
                raise failure
            delay = retry_after if retry_after is not None else self.settings.backoff_seconds * 2 ** (attempt - 1)
            await self._sleep(min(delay, self.settings.max_backoff_seconds))

        raise AssertionError("unreachable")  # pragma: no cover

    def _success(
        self, resp: httpx.Response, method: str, path: str, attempt: int, started: float, trace_id: str
    ) -> ApiResponse:
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        try:
            data = resp.json()
        except ValueError as exc:
            raise AlarmApiError(
                "Alarm API returned a non-JSON response.", status_code=resp.status_code, trace_id=trace_id
            ) from exc
        log.info(
            "alarm api call",
            extra={
                "fields": {
                    "trace_id": trace_id,
                    "method": method,
                    "path": path,
                    "status": resp.status_code,
                    "attempts": attempt,
                    "duration_ms": duration_ms,
                }
            },
        )
        return ApiResponse(data, resp.status_code, attempt, duration_ms, trace_id, method, path)

    @staticmethod
    def _error_from_response(resp: httpx.Response) -> AlarmApiError:
        cls = error_for_status(resp.status_code)
        try:
            body = resp.json().get("error", {})
        except (ValueError, AttributeError):
            body = {}  # e.g. an HTML page from a proxy - never echo it back
        return cls(
            body.get("message") or f"Alarm API returned HTTP {resp.status_code}.",
            status_code=resp.status_code,
            code=body.get("code"),
            details=body.get("details"),
            trace_id=body.get("trace_id") or resp.headers.get("x-trace-id"),
        )

    @staticmethod
    def _retry_after(resp: httpx.Response) -> float | None:
        try:
            return float(resp.headers["Retry-After"])
        except (KeyError, ValueError):
            return None

    # ----------------------------------------------------------- endpoints
    async def health(self, *, trace_id: str | None = None) -> ApiResponse:
        return await self._request("GET", "/health", trace_id=trace_id)

    async def search_assets(
        self,
        query: str,
        *,
        limit: int = 10,
        site: str | None = None,
        unit: str | None = None,
        asset_type: str | None = None,
        trace_id: str | None = None,
    ) -> ApiResponse:
        params = {"query": query, "limit": limit, "site": site, "unit": unit, "asset_type": asset_type}
        return await self._request("GET", "/assets/search", params=params, trace_id=trace_id)

    async def get_asset_metadata(self, asset_id: str, *, trace_id: str | None = None) -> ApiResponse:
        return await self._request("GET", f"/assets/{_segment(asset_id, 'asset_id')}/metadata", trace_id=trace_id)

    async def list_alarms(
        self,
        *,
        asset_id: str | Sequence[str] | None = None,
        site: str | None = None,
        unit: str | None = None,
        status: str | None = None,
        severity: str | Sequence[str] | None = None,
        alarm_type: str | Sequence[str] | None = None,
        alarm_code: str | Sequence[str] | None = None,
        start_time: datetime | str | None = None,
        end_time: datetime | str | None = None,
        page: int = 1,
        page_size: int = 50,
        sort_by: str = "start_time",
        sort_order: str = "desc",
        trace_id: str | None = None,
    ) -> ApiResponse:
        params = {
            "asset_id": _csv(asset_id),
            "site": site,
            "unit": unit,
            "status": status,
            "severity": _csv(severity),
            "alarm_type": _csv(alarm_type),
            "alarm_code": _csv(alarm_code),
            "start_time": _iso(start_time),
            "end_time": _iso(end_time),
            "page": page,
            "page_size": page_size,
            "sort_by": sort_by,
            "sort_order": sort_order,
        }
        return await self._request("GET", "/alarms", params=params, trace_id=trace_id)

    async def list_all_alarms(self, *, max_items: int = 1000, page_size: int = 200, **filters: Any) -> PageResult:
        """Follow pagination until the last page or max_items, whichever comes first."""
        trace_id = filters.pop("trace_id", None) or new_trace_id()
        result = PageResult(trace_id=trace_id)
        page, has_next = 1, True
        while has_next and len(result.items) < max_items:
            resp = await self.list_alarms(page=page, page_size=page_size, trace_id=trace_id, **filters)
            result.items.extend(resp.data["data"])
            result.pages_fetched += 1
            pagination = resp.data.get("pagination", {})
            result.total_items = pagination.get("total_items", result.total_items)
            has_next = bool(pagination.get("has_next"))
            page += 1
        if len(result.items) > max_items or (len(result.items) >= max_items and has_next):
            result.truncated = True
            result.items = result.items[:max_items]
        return result

    async def get_alarm(self, alarm_id: str, *, trace_id: str | None = None) -> ApiResponse:
        return await self._request("GET", f"/alarms/{_segment(alarm_id, 'alarm_id')}", trace_id=trace_id)

    async def alarm_summary(self, body: dict[str, Any], *, trace_id: str | None = None) -> ApiResponse:
        return await self._request("POST", "/alarms/summary", json=body, trace_id=trace_id)

    async def alarm_trends(self, body: dict[str, Any], *, trace_id: str | None = None) -> ApiResponse:
        return await self._request("POST", "/alarms/trends", json=body, trace_id=trace_id)

    async def correlate_alarms(self, body: dict[str, Any], *, trace_id: str | None = None) -> ApiResponse:
        return await self._request("POST", "/alarms/correlation", json=body, trace_id=trace_id)

    async def flood_analysis(self, body: dict[str, Any], *, trace_id: str | None = None) -> ApiResponse:
        return await self._request("POST", "/alarms/flood-analysis", json=body, trace_id=trace_id)

    async def rationalization_candidates(self, body: dict[str, Any], *, trace_id: str | None = None) -> ApiResponse:
        return await self._request("POST", "/alarms/rationalization-candidates", json=body, trace_id=trace_id)

    async def priority_score(self, alarm_id: str, *, trace_id: str | None = None) -> ApiResponse:
        _segment(alarm_id, "alarm_id")
        return await self._request("POST", "/alarms/priority-score", json={"alarm_id": alarm_id}, trace_id=trace_id)

    async def operator_recommendations(
        self,
        alarm_id: str,
        *,
        include_related: bool = False,
        include_asset_context: bool = False,
        include_historical_pattern: bool = False,
        trace_id: str | None = None,
    ) -> ApiResponse:
        _segment(alarm_id, "alarm_id")
        body = {
            "alarm_id": alarm_id,
            "include_related": include_related,
            "include_asset_context": include_asset_context,
            "include_historical_pattern": include_historical_pattern,
        }
        return await self._request("POST", "/recommendations/operator-actions", json=body, trace_id=trace_id)

    async def generate_calculation(
        self, calculation_type: str, filters: dict[str, Any] | None = None, *, trace_id: str | None = None
    ) -> ApiResponse:
        body = {"calculation_type": calculation_type, "filters": filters or {}}
        return await self._request("POST", "/calculation-code/generate", json=body, trace_id=trace_id)

    async def execute_calculation(
        self, calculation_id: str, filters: dict[str, Any] | None = None, *, trace_id: str | None = None
    ) -> ApiResponse:
        _segment(calculation_id, "calculation_id")
        body: dict[str, Any] = {"calculation_id": calculation_id}
        if filters is not None:
            body["filters"] = filters
        return await self._request("POST", "/calculation-code/execute", json=body, trace_id=trace_id)

    async def kpi_definitions(self, *, trace_id: str | None = None) -> ApiResponse:
        return await self._request("GET", "/analytics/kpi-definitions", trace_id=trace_id)
