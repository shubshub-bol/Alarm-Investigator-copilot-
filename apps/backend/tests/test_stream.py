"""Streaming chat: each real step is sent as a Server-Sent Event while the investigation runs."""

import json

from fastapi.testclient import TestClient

from copilot.api import create_app
from copilot.config import CopilotSettings
from copilot.mcp_client import in_memory_connector
from copilot.service import CopilotService
from copilot_test_helpers import simulator_transport
from mcp_test_helpers import make_server

ACCEPTANCE = "Investigate recurring high-severity alarms for Boiler Feed Pump 101 over the last 90 days."


def read_events(client: TestClient, body: dict) -> list[tuple[str, dict]]:
    events = []
    with client.stream("POST", "/api/chat/stream", json=body) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        event = None
        for line in response.iter_lines():
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                events.append((event, json.loads(line[len("data: ") :])))
    return events


def make_client(retriever) -> TestClient:
    service = CopilotService(in_memory_connector(make_server(simulator_transport())), retriever, llm=None)
    return TestClient(create_app(CopilotSettings(_env_file=None), service=service))


def test_stream_sends_steps_in_order_then_the_answer(retriever):
    with make_client(retriever) as client:
        events = read_events(client, {"message": ACCEPTANCE})
    types = [t for t, _ in events]
    assert types[:3] == ["started", "understanding", "discovery"]
    assert types[-1] == "answer"
    steps = [data for t, data in events if t == "step"]
    assert len(steps) >= 8
    assert [s["step"] for s in steps] == list(range(1, len(steps) + 1))
    assert steps[0]["tool"] == "search_assets" and steps[0]["status"] == "ok"

    started, answer = events[0][1], events[-1][1]
    assert answer["trace_id"] == started["trace_id"]
    assert answer["conversation_id"] == started["conversation_id"]
    assert len(answer["trace"]) == len(steps)  # the final answer carries the same steps
    assert answer["answer"]["citations"]


def test_stream_continues_a_conversation(retriever):
    with make_client(retriever) as client:
        first = read_events(client, {"message": ACCEPTANCE})[-1][1]
        follow = read_events(
            client,
            {
                "message": "Which operating procedure applies to this alarm?",
                "conversation_id": first["conversation_id"],
            },
        )
    assert follow[0][1]["conversation_id"] == first["conversation_id"]
    assert [d for t, d in follow if t == "step"][0]["tool"] == "get_alarm_details"


def test_stream_rejects_invalid_input(retriever):
    with make_client(retriever) as client:
        assert client.post("/api/chat/stream", json={"message": ""}).status_code == 422


class ExplodingService(CopilotService):
    async def ask(self, question, conversation_id=None, on_event=None):
        await on_event("started", {"trace_id": "t", "conversation_id": "c"})
        raise RuntimeError("database on fire")


def test_unexpected_failure_ends_the_stream_with_an_error_event(retriever):
    service = ExplodingService(in_memory_connector(make_server(simulator_transport())), retriever, llm=None)
    with TestClient(create_app(CopilotSettings(_env_file=None), service=service)) as client:
        events = read_events(client, {"message": "anything"})
    assert events[-1][0] == "error"
    assert "database on fire" not in events[-1][1]["message"]  # internals are not leaked to the browser
    assert events[-1][1]["trace_id"] == "t"
