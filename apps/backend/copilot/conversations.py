"""Saved conversations (SQLite): titles, every answered turn and the follow-up context.

One file, created on first use. Each call opens its own short-lived connection, so the store works from
FastAPI's thread pool and from asyncio.to_thread. A chat is written only when an answer is ready, so failed or
cancelled questions never leave empty chats behind.
"""

import json
import logging
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from copilot.investigation import ConversationState

log = logging.getLogger("copilot.conversations")

SCHEMA_VERSION = 1  # also written by the literal "PRAGMA user_version = 1" below
PLACEHOLDER_TITLE = "New chat"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    title_source TEXT NOT NULL CHECK (title_source IN ('placeholder', 'auto', 'user')),
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    state        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS turns (
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    seq             INTEGER NOT NULL,
    question        TEXT NOT NULL,
    response        TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (conversation_id, seq)
);
"""


class StorageError(Exception):
    """The conversation database could not be opened, read or written."""


class ConversationSummary(BaseModel):
    conversation_id: str
    title: str
    created_at: datetime
    updated_at: datetime


class StoredTurn(BaseModel):
    question: str
    created_at: datetime
    response: dict[str, Any]  # that turn's ChatResponse, exactly as the browser received it


class ConversationDetail(ConversationSummary):
    turns: list[StoredTurn]


@dataclass
class OpenConversation:
    """The conversation a question belongs to. A new one exists only in memory until its first answer is saved."""

    conversation_id: str
    state: ConversationState = field(default_factory=ConversationState)
    needs_title: bool = True  # still "New chat": the next real question names it


def new_conversation_id() -> str:
    return f"conv-{uuid.uuid4().hex[:12]}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _summary(row: sqlite3.Row) -> ConversationSummary:
    return ConversationSummary(
        conversation_id=row["id"], title=row["title"], created_at=row["created_at"], updated_at=row["updated_at"]
    )


class ConversationStore:
    def __init__(self, path: Path | str | None = None):
        """path=None keeps everything in memory (tests); a file and its folder are created if missing.

        A database that cannot be opened or has another schema version does not stop the backend: the store
        reports it in health() and refuses every call, so answers still work, just without being saved.
        """
        self.error: str | None = None  # the last storage failure, reported by health()
        self._unusable: str | None = None
        self._anchor: sqlite3.Connection | None = None
        if path is None:
            self._target, self._uri = f"file:conversations-{uuid.uuid4().hex}?mode=memory&cache=shared", True
            # an in-memory database lives as long as one connection to it is open
            self._anchor = sqlite3.connect(self._target, uri=True, check_same_thread=False)
        else:
            self._target, self._uri = str(path), False
        try:
            if path is not None:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._create_schema()
        except (OSError, StorageError) as exc:
            self._unusable = self.error = str(exc)
            log.error("conversation storage unavailable", extra={"fields": {"path": str(path), "error": str(exc)}})

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """One transaction on a fresh connection: committed on success, rolled back on any error."""
        if self._unusable:
            raise StorageError(self._unusable)
        try:
            conn = sqlite3.connect(self._target, uri=self._uri, timeout=5)
        except sqlite3.Error as exc:
            raise StorageError(f"cannot open the conversation database: {exc}") from exc
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:
                yield conn
        except sqlite3.Error as exc:
            raise StorageError(f"conversation database error: {exc}") from exc
        finally:
            conn.close()

    def _create_schema(self) -> None:
        with self._connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise StorageError(
                    f"the conversation database has schema version {version}; this backend expects {SCHEMA_VERSION}"
                )
            if not self._uri:
                conn.execute("PRAGMA journal_mode = WAL")  # the list can be read while an answer is being saved
            conn.executescript(_SCHEMA)
            conn.execute("PRAGMA user_version = 1")

    def resume(self, conversation_id: str | None) -> OpenConversation:
        """The saved conversation with this id, or a new one (no id, or an id this store never saved)."""
        if conversation_id:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT state, title_source FROM conversations WHERE id = ?", (conversation_id,)
                ).fetchone()
            if row is not None:
                try:
                    state = ConversationState.model_validate_json(row["state"])
                except ValidationError as exc:  # damaged, or saved by an older version: only the context is lost
                    log.warning(
                        "saved follow-up context unreadable; continuing without it",
                        extra={"fields": {"conversation_id": conversation_id, "error": str(exc)[:300]}},
                    )
                    state = ConversationState()
                return OpenConversation(
                    conversation_id=conversation_id,
                    state=state,
                    needs_title=row["title_source"] == "placeholder",
                )
        return OpenConversation(conversation_id=new_conversation_id())

    def save_turn(self, conv: OpenConversation, question: str, response: dict[str, Any], title: str | None) -> None:
        """Append one answered turn in one transaction; the chat row is created with its first answer.

        `title` names a chat that is still "New chat"; it is ignored once the chat has an automatic or user title.
        """
        now = _now()
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO conversations (id, title, title_source, created_at, updated_at, state) "
                    "VALUES (?, ?, 'placeholder', ?, ?, ?) "
                    "ON CONFLICT (id) DO UPDATE SET updated_at = excluded.updated_at, state = excluded.state",
                    (conv.conversation_id, PLACEHOLDER_TITLE, now, now, conv.state.model_dump_json()),
                )
                if title:
                    conn.execute(
                        "UPDATE conversations SET title = ?, title_source = 'auto' "
                        "WHERE id = ? AND title_source = 'placeholder'",
                        (title, conv.conversation_id),
                    )
                conn.execute(
                    "INSERT INTO turns (conversation_id, seq, question, response, created_at) "
                    "SELECT ?, COALESCE(MAX(seq), 0) + 1, ?, ?, ? FROM turns WHERE conversation_id = ?",
                    (conv.conversation_id, question, json.dumps(response), now, conv.conversation_id),
                )
        except StorageError as exc:
            self.error = str(exc)
            raise
        self.error = None
        if title:
            conv.needs_title = False

    def list_conversations(self) -> list[ConversationSummary]:
        """Newest activity first. Renaming does not change the order: only a new turn moves a chat up.

        Ordered by when each chat's latest turn was saved (turn rowids only grow), not by the clock: on Windows two
        quick saves can share a timestamp.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT c.id, c.title, c.created_at, c.updated_at FROM conversations c "
                "ORDER BY (SELECT MAX(t.rowid) FROM turns t WHERE t.conversation_id = c.id) DESC"
            ).fetchall()
        return [_summary(row) for row in rows]

    def get(self, conversation_id: str) -> ConversationDetail | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, title, created_at, updated_at FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            if row is None:
                return None
            turns = conn.execute(
                "SELECT question, created_at, response FROM turns WHERE conversation_id = ? ORDER BY seq",
                (conversation_id,),
            ).fetchall()
        return ConversationDetail(
            **_summary(row).model_dump(),
            turns=[
                StoredTurn(question=t["question"], created_at=t["created_at"], response=json.loads(t["response"]))
                for t in turns
            ],
        )

    def rename(self, conversation_id: str, title: str) -> ConversationSummary | None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE conversations SET title = ?, title_source = 'user' WHERE id = ?", (title, conversation_id)
            )
            row = conn.execute(
                "SELECT id, title, created_at, updated_at FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
        return _summary(row) if row else None

    def delete(self, conversation_id: str) -> bool:
        """Deletes the chat and, through the foreign key, its turns. False if there was no such chat."""
        with self._connect() as conn:
            return conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,)).rowcount > 0

    def health(self) -> dict[str, Any]:
        try:
            with self._connect() as conn:
                count = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        except StorageError as exc:
            return {"ok": False, "error": str(exc)}
        if self.error:
            return {"ok": False, "error": self.error, "conversations": count}
        return {"ok": True, "conversations": count}
