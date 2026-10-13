"""Helpers shared by the copilot tests."""

import httpx

from alarm_simulator.config import Settings as SimulatorSettings
from alarm_simulator.main import create_app
from mcp_test_helpers import API_TOKEN, RecordingTransport


def simulator_transport() -> RecordingTransport:
    return RecordingTransport(httpx.ASGITransport(app=create_app(SimulatorSettings(api_token=API_TOKEN))))


class EditedResults:
    """The real MCP client, with fields of one tool's result replaced: a result the simulator cannot give."""

    def __init__(self, real, tool, **fields):
        self.real, self.tool, self.fields = real, tool, fields

    async def call(self, tool, arguments, **kwargs):
        record = await self.real.call(tool, arguments, **kwargs)
        if tool == self.tool and record.result is not None:
            record.result = {**record.result, **self.fields}
        return record
