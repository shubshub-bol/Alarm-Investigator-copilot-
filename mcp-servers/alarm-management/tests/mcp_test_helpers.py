"""Shared helpers for the MCP server tests."""

import httpx

from alarm_api_client import AlarmApiClient, ClientSettings
from alarm_mcp.server import build_server

API_TOKEN = "sim-token"


class RecordingTransport(httpx.AsyncBaseTransport):
    """Wraps a transport and keeps every request, so tests can inspect headers sent to the API."""

    def __init__(self, inner: httpx.AsyncBaseTransport):
        self.inner = inner
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return await self.inner.handle_async_request(request)


def make_server(transport: httpx.AsyncBaseTransport, token: str = API_TOKEN, **client_overrides):
    settings = ClientSettings(base_url="http://simulator", token=token, backoff_seconds=0, **client_overrides)
    return build_server(api_client_factory=lambda: AlarmApiClient(settings, transport=transport))
