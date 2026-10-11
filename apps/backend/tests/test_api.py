"""Backend HTTP API: chat, follow-ups, tool discovery view, health, validation and degraded modes."""

import pytest
from fastapi.testclient import TestClient

from copilot.api import create_app
from copilot.config import CopilotSettings
from copilot.llm import LlmError
from copilot.mcp_client import http_connector, in_memory_connector
from copilot.service import CopilotService
from copilot_test_helpers import simulator_transport
from mcp_test_helpers import make_server

ACCEPTANCE = (
    "Investigate recurring high-severity alarms for Boiler Feed Pump 101 over the last 90 days, identify likely "
    "contributing factors, retrieve the relevant operating procedure, and provide recommended actions."
)


def client_for(retriever, connector=None, llm=None) -> TestClient:
    connector = connector or in_memory_connector(make_server(simulator_transport()))
    service = CopilotService(connector=connector, retriever=retriever, llm=llm)
    return TestClient(create_app(CopilotSettings(_env_file=None), service=service))


@pytest.fixture
def api(retriever):
    with client_for(retriever) as client:
        yield client


# ------------------------------------------------------------------- happy path


def test_health_reports_all_dependencies(api):
    body = api.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["mcp"] == {"server": "alarm-management", "connected": True, "tools": 13, "error": None}
    assert body["rag"]["index_loaded"] and body["rag"]["chunks"] > 40
    assert body["llm"] == {"provider": "offline"}
    assert body["storage"] == {"ok": True, "conversations": 0}


def test_tool_discovery_view_has_schemas(api):
    body = api.get("/api/tools").json()
    assert body["connected"] is True
    tools = {t["name"]: t for t in body["tools"]}
    assert len(tools) == 13
    assert tools["correlate_alarms"]["input_schema"]["required"] == ["asset_ids"]
    assert tools["correlate_alarms"]["output_schema"]["type"] == "object"


def test_chat_returns_grounded_answer_with_full_trace(api):
    r = api.post("/api/chat", json={"message": ACCEPTANCE})
    assert r.status_code == 200
    assert r.headers["x-request-id"].startswith("req-")
    body = r.json()
    assert body["status"] == "ok"
    assert body["conversation_id"].startswith("conv-")
    assert body["understanding"]["intent"] == "investigate_asset"
    assert body["discovery"]["tool_count"] == 13
    kinds = [s["kind"] for s in body["trace"]]
    assert kinds.count("mcp_tool") >= 8 and "rag_retrieval" in kinds
    tool_steps = [s for s in body["trace"] if s["kind"] == "mcp_tool"]
    assert all(s["result"]["meta"]["trace_id"] == body["trace_id"] for s in tool_steps)  # trace propagated
    assert tool_steps[0]["api_calls"][0]["path"] == "/assets/search"  # raw request/response is inspectable
    answer = body["answer"]
    assert "[S" in answer["markdown"] and "[T" in answer["markdown"]
    assert any(a["verdict"] == "conflict" for a in answer["action_assessments"])
    assert body["evidence"]["asset"]["asset_id"] == "AST-BFP-101"
    assert body["timings"]["total_ms"] > 0


def test_follow_up_question_uses_conversation_context(api):
    first = api.post("/api/chat", json={"message": ACCEPTANCE}).json()
    follow = api.post(
        "/api/chat",
        json={
            "message": "Which operating procedure applies to this alarm?",
            "conversation_id": first["conversation_id"],
        },
    ).json()
    assert follow["conversation_id"] == first["conversation_id"]
    assert follow["trace"][0]["tool"] == "get_alarm_details"
    history = api.get(f"/api/conversations/{first['conversation_id']}").json()
    assert [t["response"]["understanding"]["intent"] for t in history["turns"]] == [
        "investigate_asset",
        "procedure_lookup",
    ]


def test_unknown_conversation_starts_a_new_one(api):
    body = api.post(
        "/api/chat", json={"message": "Boiler Feed Pump 101", "conversation_id": "conv-000000000000"}
    ).json()
    assert body["conversation_id"] != "conv-000000000000"


def test_examples_endpoint(api):
    assert len(api.get("/api/examples").json()["examples"]) >= 3


def test_cors_allows_the_frontend(api):
    r = api.options("/api/chat", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"


# ------------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "body",
    [{"message": ""}, {"message": "x" * 2001}, {"message": "hi", "conversation_id": "../../etc"}, {}],
)
def test_invalid_requests_are_rejected(api, body):
    assert api.post("/api/chat", json=body).status_code == 422


def test_unknown_conversation_lookup_is_404(api):
    assert api.get("/api/conversations/conv-000000000000").status_code == 404


# ------------------------------------------------------------------ degraded modes


def test_mcp_server_down_degrades_to_documents(retriever):
    with client_for(retriever, connector=http_connector("http://127.0.0.1:9/mcp", "x", timeout_seconds=2)) as api:
        health = api.get("/api/health").json()
        body = api.post("/api/chat", json={"message": ACCEPTANCE}).json()
    assert health["status"] == "degraded"
    assert health["mcp"]["connected"] is False
    assert body["status"] == "degraded"
    assert body["discovery"]["connected"] is False
    assert body["answer"]["citations"]  # documents still answer
    assert any("MCP server is unavailable" in g for g in body["answer"]["gaps"])


class BrokenLlm:
    name = "broken"

    async def generate_json(self, system, prompt, schema):
        raise LlmError("429 RESOURCE_EXHAUSTED")

    async def generate_text(self, system, prompt, temperature=None):
        raise LlmError("429 RESOURCE_EXHAUSTED")


def test_llm_outage_degrades_to_rules_and_template(retriever):
    with client_for(retriever, llm=BrokenLlm()) as api:
        body = api.post("/api/chat", json={"message": ACCEPTANCE}).json()
    assert body["status"] == "degraded"
    assert body["understanding"]["method"] == "rules"
    assert body["answer"]["grounding"]["degraded"] is True
    assert "RESOURCE_EXHAUSTED" in body["answer"]["grounding"]["degraded_reason"]
    assert body["answer"]["recommended_actions"]  # still a useful answer


# ------------------------------------------------------------------ conversation


def test_greeting_is_answered_without_tools_or_documents(retriever):
    transport = simulator_transport()
    service = CopilotService(in_memory_connector(make_server(transport)), retriever, llm=BrokenLlm())
    with TestClient(create_app(CopilotSettings(_env_file=None), service=service)) as api:
        body = api.post("/api/chat", json={"message": "hello"}).json()
    assert body["understanding"]["intent"] == "conversation"
    assert body["trace"] == []
    assert body["discovery"] is None  # no MCP session was opened
    assert transport.requests == []  # nothing reached the Alarm API
    assert body["status"] == "ok"  # the fixed reply stands in for the failed language model
    assert body["answer"]["grounding"]["mode"] == "template"
    assert body["answer"]["citations"] == []
    assert "alarm" in body["answer"]["summary"].lower()
    assert len(body["answer"]["suggestions"]) >= 3


def test_investigation_answers_offer_follow_up_questions(api):
    body = api.post("/api/chat", json={"message": ACCEPTANCE}).json()
    suggestions = body["answer"]["suggestions"]
    assert "Which operating procedure applies to this alarm?" in suggestions
    assert all("?" in s for s in suggestions)


def test_examples_are_standalone_questions(api):
    examples = api.get("/api/examples").json()["examples"]
    assert examples
    assert not any("this alarm" in e or "the API recommendations" in e for e in examples)
