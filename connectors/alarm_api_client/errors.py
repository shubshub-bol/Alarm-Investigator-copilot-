"""Typed errors. Callers branch on the class (or .retryable), never on raw HTTP status codes."""

from typing import Any


class AlarmApiError(Exception):
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        details: Any = None,
        trace_id: str | None = None,
        attempts: int = 1,
        method: str | None = None,
        path: str | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.details = details
        self.trace_id = trace_id
        self.attempts = attempts
        self.method = method
        self.path = path

    def __str__(self) -> str:
        return f"{self.message} (status={self.status_code}, code={self.code}, trace_id={self.trace_id})"


class AuthenticationError(AlarmApiError):
    """401/403 - token missing, wrong or not allowed."""


class NotFoundError(AlarmApiError):
    """404 - the asset/alarm/calculation does not exist."""


class RequestValidationError(AlarmApiError):
    """400/422 - the request was rejected; .details lists the problems."""


class RateLimitedError(AlarmApiError):
    """429 - too many requests; safe to retry later."""

    retryable = True


class UpstreamUnavailableError(AlarmApiError):
    """5xx or network failure - the API is down or unreachable; safe to retry."""

    retryable = True


class ApiTimeoutError(UpstreamUnavailableError):
    """The API did not answer within the configured timeout."""


def error_for_status(status: int) -> type[AlarmApiError]:
    if status in (401, 403):
        return AuthenticationError
    if status == 404:
        return NotFoundError
    if status in (400, 422):
        return RequestValidationError
    if status == 429:
        return RateLimitedError
    if status >= 500:
        return UpstreamUnavailableError
    return AlarmApiError
