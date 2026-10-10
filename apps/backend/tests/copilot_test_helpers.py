"""Helpers shared by the copilot tests."""

import httpx

from alarm_simulator.config import Settings as SimulatorSettings
from alarm_simulator.main import create_app
from mcp_test_helpers import API_TOKEN, RecordingTransport


def simulator_transport() -> RecordingTransport:
    return RecordingTransport(httpx.ASGITransport(app=create_app(SimulatorSettings(api_token=API_TOKEN))))
