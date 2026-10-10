"""Trace-id propagation and structured JSON request logs."""

import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response

log = logging.getLogger("alarm_simulator")

# Postman sends "trace_id"; many clients send "x-trace-id". Accept both.
INBOUND_TRACE_HEADERS = ("trace_id", "x-trace-id")
OUTBOUND_TRACE_HEADER = "x-trace-id"
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            **getattr(record, "fields", {}),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    log.handlers = [handler]
    log.setLevel(level.upper())
    log.propagate = False


def new_trace_id() -> str:
    return f"trace-{uuid.uuid4().hex[:16]}"


def _clean(value: str | None) -> str | None:
    """Only accept short, safe ids - never copy arbitrary header text into logs."""
    return value if value and _SAFE_ID.match(value) else None


async def trace_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    inbound = next((request.headers[h] for h in INBOUND_TRACE_HEADERS if h in request.headers), None)
    trace_id = _clean(inbound) or new_trace_id()
    request.state.trace_id = trace_id
    client_id = _clean(request.headers.get("x-client-id"))

    started = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - started) * 1000, 1)

    response.headers[OUTBOUND_TRACE_HEADER] = trace_id
    # Authorization and bodies are deliberately never logged.
    log.info(
        "request",
        extra={
            "fields": {
                "trace_id": trace_id,
                "client_id": client_id,
                "metadata_tag": _clean(request.headers.get("x-metadata-tag")),
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
            }
        },
    )
    return response
