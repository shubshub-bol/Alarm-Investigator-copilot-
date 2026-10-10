"""Reusable async connector for the Alarm Management API."""

from alarm_api_client.client import AlarmApiClient, ApiResponse, PageResult, new_trace_id
from alarm_api_client.config import ClientSettings
from alarm_api_client.errors import (
    AlarmApiError,
    ApiTimeoutError,
    AuthenticationError,
    NotFoundError,
    RateLimitedError,
    RequestValidationError,
    UpstreamUnavailableError,
)

__all__ = [
    "AlarmApiClient",
    "AlarmApiError",
    "ApiResponse",
    "ApiTimeoutError",
    "AuthenticationError",
    "ClientSettings",
    "NotFoundError",
    "PageResult",
    "RateLimitedError",
    "RequestValidationError",
    "UpstreamUnavailableError",
    "new_trace_id",
]
