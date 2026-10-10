"""Saved chats over HTTP: list, open, rename, delete, validation, restarts and storage failures."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from copilot.api import create_app
from copilot.config import CopilotSettings
from copilot.conversations import ConversationStore
from copilot.mcp_client import in_memory_connector
from copilot.service import CopilotService
from copilot_test_helpers import simulator_transport
from mcp_test_helpers import make_server

ACCEPTANCE = (
    "Investigate recurring high-severity alarms for Boiler Feed Pump 101 over the last 90 days, identify likely "
    "contributing factors, retrieve the relevant operating procedure, and provide recommended actions."
)
FOLLOW_UP = "Which operating procedure applies to this alarm?"


def backend(retriever, path) -> TestClient:
    """A backend whose chats live in `path`. Building a second one on the same path is a restart."""
    service = CopilotService(
        in_memory_connector(make_server(simulator_transport())),
        retriever,
        llm=None,
        conversations=ConversationStore(path),
    )
    return TestClient(create_app(CopilotSettings(_env_file=None), service=service))


@pytest.fixture
def db(tmp_path):
    return tmp_path / "chats.sqlite3"


def test_a_question_creates_a_named_chat(retriever, db):
    with backend(retriever, db) as api:
        body = api.post("/api/chat", json={"message": ACCEPTANCE}).json()
        [chat] = api.get("/api/conversations").json()["conversations"]
    assert chat["conversation_id"] == body["conversation_id"]
    assert chat["title"] == "Boiler Feed Pump 101: High Vibration"
    assert set(chat) == {"conversation_id", "title", "created_at", "updated_at"}


def test_a_chat_opens_with_every_answer(retriever, db):
    with backend(retriever, db) as api:
        first = api.post("/api/chat", json={"message": ACCEPTANCE}).json()
        api.post("/api/chat", json={"message": FOLLOW_UP, "conversation_id": first["conversation_id"]})
        detail = api.get(f"/api/conversations/{first['conversation_id']}").json()
    assert [t["question"] for t in detail["turns"]] == [ACCEPTANCE, FOLLOW_UP]
    assert detail["turns"][0]["response"]["trace_id"] == first["trace_id"]


def test_chats_and_follow_up_context_survive_a_restart(retriever, db):
    with backend(retriever, db) as api:
        first = api.post("/api/chat", json={"message": ACCEPTANCE}).json()
    with backend(retriever, db) as restarted:
        listed = restarted.get("/api/conversations").json()["conversations"]
        follow = restarted.post(
            "/api/chat", json={"message": FOLLOW_UP, "conversation_id": first["conversation_id"]}
        ).json()
    assert [c["conversation_id"] for c in listed] == [first["conversation_id"]]
    assert follow["conversation_id"] == first["conversation_id"]
    assert follow["trace"][0]["tool"] == "get_alarm_details"  # 'this alarm' was remembered across the restart


def test_rename_and_delete(retriever, db):
    with backend(retriever, db) as api:
        conv_id = api.post("/api/chat", json={"message": "hello"}).json()["conversation_id"]
        renamed = api.patch(f"/api/conversations/{conv_id}", json={"title": "  Shift handover  "})
        deleted = api.delete(f"/api/conversations/{conv_id}")
        reopened = api.get(f"/api/conversations/{conv_id}")
        listed = api.get("/api/conversations").json()["conversations"]
    assert renamed.status_code == 200 and renamed.json()["title"] == "Shift handover"
    assert deleted.status_code == 204
    assert reopened.status_code == 404
    assert listed == []


@pytest.mark.parametrize(
    ("method", "path", "body", "status"),
    [
        ("get", "/api/conversations/conv-000000000000", None, 404),
        ("patch", "/api/conversations/conv-000000000000", {"title": "x"}, 404),
        ("delete", "/api/conversations/conv-000000000000", None, 404),
        ("get", "/api/conversations/conv-nope", None, 422),
        ("delete", "/api/conversations/CONV-000000000000", None, 422),
        ("patch", "/api/conversations/conv-000000000000", {"title": "   "}, 422),
        ("patch", "/api/conversations/conv-000000000000", {"title": "x" * 81}, 422),
        ("patch", "/api/conversations/conv-000000000000", {}, 422),
    ],
)
def test_bad_requests_are_rejected(retriever, db, method, path, body, status):
    with backend(retriever, db) as api:
        response = api.request(method.upper(), path, json=body)
    assert response.status_code == status


def test_health_reports_saved_chats(retriever, db):
    with backend(retriever, db) as api:
        api.post("/api/chat", json={"message": "hello"})
        health = api.get("/api/health").json()
    assert health["storage"] == {"ok": True, "conversations": 1}


def test_a_broken_database_degrades_health_but_answers_still_work(retriever, db):
    raw = sqlite3.connect(db)
    raw.execute("PRAGMA user_version = 99")
    raw.commit()
    raw.close()
    with backend(retriever, db) as api:
        health = api.get("/api/health").json()
        answer = api.post("/api/chat", json={"message": "hello"})
        listing = api.get("/api/conversations")
    assert health["status"] == "degraded" and health["storage"]["ok"] is False
    assert answer.status_code == 200 and answer.json()["answer"]["summary"]
    assert listing.status_code == 503
    assert listing.json() == {"detail": "Saved chats are unavailable right now. Check the backend logs."}


def test_cors_allows_rename_and_delete(retriever, db):
    with backend(retriever, db) as api:
        for method in ("PATCH", "DELETE"):
            r = api.options(
                "/api/conversations/conv-000000000000",
                headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": method},
            )
            assert r.status_code == 200
            assert method in r.headers["access-control-allow-methods"]
