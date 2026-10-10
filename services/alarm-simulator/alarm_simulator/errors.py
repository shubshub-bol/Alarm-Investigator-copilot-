"""One error envelope for every failure:

{"error": {"code": "not_found", "message": "...", "trace_id": "...", "details": {...}}}
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("alarm_simulator")

_STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    422: "validation_error",
    429: "rate_limited",
    503: "unavailable",
}


class ApiError(Exception):
    """Raise from any route to return a structured error."""

    def __init__(
        self, status: int, code: str, message: str, details: dict | None = None, headers: dict[str, str] | None = None
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers


def _trace_id(request: Request) -> str | None:
    return getattr(request.state, "trace_id", None)


def error_response(
    request: Request,
    status: int,
    code: str,
    message: str,
    details: dict | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict = {"code": code, "message": message, "trace_id": _trace_id(request)}
    if details:
        body["details"] = details
    return JSONResponse({"error": body}, status_code=status, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(request, exc.status, exc.code, exc.message, exc.details, exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, "http_error")
        return error_response(request, exc.status_code, code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Only location + message: never echo the submitted input back.
        problems = [{"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in exc.errors()]
        return error_response(request, 422, "validation_error", "Request validation failed.", {"problems": problems})

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", extra={"fields": {"trace_id": _trace_id(request)}})
        return error_response(request, 500, "internal_error", "Unexpected server error.")
