"""Fixtures: an in-memory MCP Client -> our MCP server -> connector -> simulator (ASGI, no network)."""

import httpx
import pytest
from mcp import Client

from alarm_simulator.config import Settings as SimulatorSettings
from alarm_simulator.main import create_app
from mcp_test_helpers import API_TOKEN, RecordingTransport, make_server


@pytest.fixture
def api_transport() -> RecordingTransport:
    return RecordingTransport(httpx.ASGITransport(app=create_app(SimulatorSettings(api_token=API_TOKEN))))


@pytest.fixture
async def mcp_client(api_transport):
    async with Client(make_server(api_transport)) as client:
        yield client
