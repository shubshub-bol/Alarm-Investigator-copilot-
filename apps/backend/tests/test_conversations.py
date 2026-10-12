"""Saved conversations: the SQLite store, including a restart on the same file and storage failures."""

import sqlite3

import pytest

from copilot.conversations import PLACEHOLDER_TITLE, ConversationStore, OpenConversation, StorageError
from copilot.investigation import ConversationState
from copilot.mcp_client import in_memory_connector
from copilot.service import CopilotService
from copilot_test_helpers import simulator_transport
from mcp_test_helpers import make_server


def answer(text: str = "ok") -> dict:
    return {"answer": {"summary": text}}


def raw(path, sql: str) -> list:
    """Run SQL on the file directly, bypassing the store (to inspect it or to break it)."""
    db = sqlite3.connect(path)
    try:
        rows = db.execute(sql).fetchall()
        db.commit()
        return rows
    finally:
        db.close()


@pytest.fixture
def db(tmp_path):
    return tmp_path / "chats.sqlite3"


@pytest.fixture
def store(db) -> ConversationStore:
    return ConversationStore(db)


def test_a_new_conversation_is_listed_only_after_its_first_answer(store):
    conv = store.resume(None)
    assert conv.conversation_id.startswith("conv-") and len(conv.conversation_id) == 17
    assert store.list_conversations() == []
    store.save_turn(conv, "hello", answer(), title=None)
    [summary] = store.list_conversations()
    assert summary.conversation_id == conv.conversation_id
    assert summary.title == PLACEHOLDER_TITLE


def test_turns_keep_their_order_and_full_response(store):
    conv = store.resume(None)
    store.save_turn(conv, "first", answer("one"), title="Pump Alarms")
    store.save_turn(conv, "second", answer("two"), title=None)
    detail = store.get(conv.conversation_id)
    assert [t.question for t in detail.turns] == ["first", "second"]
    assert detail.turns[1].response == answer("two")


def test_chats_and_follow_up_context_survive_a_restart(db):
    conv = ConversationStore(db).resume(None)
    conv.state = ConversationState(last_asset={"asset_id": "AST-BFP-101", "name": "Boiler Feed Pump 101"})
    ConversationStore(db).save_turn(conv, "q", answer(), title="Boiler Feed Pump 101: High Vibration")

    restarted = ConversationStore(db)
    resumed = restarted.resume(conv.conversation_id)
    assert resumed.state.last_asset["asset_id"] == "AST-BFP-101"
    assert resumed.needs_title is False
    assert [c.title for c in restarted.list_conversations()] == ["Boiler Feed Pump 101: High Vibration"]


def test_unknown_ids_start_a_new_conversation(store):
    assert store.resume("conv-000000000000").conversation_id != "conv-000000000000"


def test_small_talk_keeps_the_placeholder_until_the_first_real_title(store):
    conv = store.resume(None)
    store.save_turn(conv, "hello", answer(), title=None)
    assert store.resume(conv.conversation_id).needs_title is True
    store.save_turn(conv, "Investigate BFP-101", answer(), title="Boiler Feed Pump 101: High Vibration")
    store.save_turn(conv, "and now?", answer(), title="Something Else")  # named once, never renamed by the copilot
    assert store.get(conv.conversation_id).title == "Boiler Feed Pump 101: High Vibration"


def test_a_user_title_is_never_replaced(store):
    conv = store.resume(None)
    store.save_turn(conv, "hello", answer(), title=None)
    store.rename(conv.conversation_id, "My chat")
    store.save_turn(store.resume(conv.conversation_id), "Investigate BFP-101", answer(), title="Auto Title")
    assert store.get(conv.conversation_id).title == "My chat"


def test_rename_keeps_the_list_order(store):
    older, newer = store.resume(None), store.resume(None)
    store.save_turn(older, "a", answer(), title="Older")
    store.save_turn(newer, "b", answer(), title="Newer")
    store.rename(older.conversation_id, "Renamed")
    assert [c.title for c in store.list_conversations()] == ["Newer", "Renamed"]


def test_chats_saved_in_the_same_clock_tick_keep_their_order(store, monkeypatch):
    monkeypatch.setattr("copilot.conversations._now", lambda: "2026-10-10T09:00:00+00:00")  # a coarse clock
    older, newer = OpenConversation("conv-aaaaaaaaaaaa"), OpenConversation("conv-bbbbbbbbbbbb")
    store.save_turn(older, "a", answer(), title="Older")
    store.save_turn(newer, "b", answer(), title="Newer")
    assert [c.title for c in store.list_conversations()] == ["Newer", "Older"]


def test_titles_are_stored_verbatim(store):
    conv = store.resume(None)
    store.save_turn(conv, "q", answer(), title=None)
    hostile = '<img src=x onerror="alert(1)"> Pump «Ü»'
    assert store.rename(conv.conversation_id, hostile).title == hostile
    assert store.list_conversations()[0].title == hostile


def test_delete_removes_the_chat_and_its_turns(store, db):
    conv = store.resume(None)
    store.save_turn(conv, "q", answer(), title="T")
    assert store.delete(conv.conversation_id) is True
    assert store.get(conv.conversation_id) is None
    assert store.delete(conv.conversation_id) is False
    assert raw(db, "SELECT COUNT(*) FROM turns") == [(0,)]


def test_rename_of_an_unknown_chat_returns_none(store):
    assert store.rename("conv-000000000000", "x") is None


def test_the_in_memory_store_needs_no_file():
    store = ConversationStore()
    conv = store.resume(None)
    store.save_turn(conv, "q", answer(), title="T")
    assert store.get(conv.conversation_id).title == "T"
    assert store.health() == {"ok": True, "conversations": 1}


def test_a_database_from_another_version_is_reported_and_not_used(db):
    raw(db, "PRAGMA user_version = 99")
    store = ConversationStore(db)
    health = store.health()
    assert health["ok"] is False and "schema version 99" in health["error"]
    with pytest.raises(StorageError):
        store.resume("conv-000000000000")


def test_a_failed_save_leaves_no_half_saved_chat_and_is_reported(store, db):
    raw(db, "DROP TABLE turns")
    with pytest.raises(StorageError):
        store.save_turn(store.resume(None), "q", answer(), title="T")
    assert raw(db, "SELECT COUNT(*) FROM conversations") == [(0,)]  # the chat row was rolled back with the turn
    assert store.health()["ok"] is False


async def test_the_answer_survives_a_storage_failure(db):
    store = ConversationStore(db)
    raw(db, "DROP TABLE turns")
    service = CopilotService(
        in_memory_connector(make_server(simulator_transport())), None, llm=None, conversations=store
    )
    response = await service.ask("hello")
    assert response.answer.summary
    assert response.saved is False
    assert store.health()["ok"] is False


async def test_a_chat_that_cannot_be_loaded_keeps_its_address(db):
    """The question is answered without the earlier context, but in the same chat, not under a new unsaved id."""
    raw(db, "PRAGMA user_version = 99")
    service = CopilotService(
        in_memory_connector(make_server(simulator_transport())), None, llm=None, conversations=ConversationStore(db)
    )
    response = await service.ask("hello", "conv-0123456789ab")
    assert response.conversation_id == "conv-0123456789ab"
    assert response.saved is False


def test_unreadable_follow_up_context_keeps_the_chat(store, db):
    """A damaged or older saved context only loses the follow-up context, not the chat."""
    conv = store.resume(None)
    store.save_turn(conv, "q", answer(), title="T")
    raw(db, "UPDATE conversations SET state = '{not json'")
    resumed = store.resume(conv.conversation_id)
    assert resumed.conversation_id == conv.conversation_id
    assert resumed.state == ConversationState()
    assert resumed.needs_title is False
