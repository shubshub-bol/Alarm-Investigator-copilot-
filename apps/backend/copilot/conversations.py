"""In-memory conversation store (context retention for follow-up questions). Bounded; oldest evicted."""

import uuid
from collections import OrderedDict
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from copilot.investigation import ConversationState


class Turn(BaseModel):
    question: str
    intent: str
    summary: str
    trace_id: str
    status: str
    at: datetime


class Conversation(BaseModel):
    conversation_id: str
    created_at: datetime
    state: ConversationState = Field(default_factory=ConversationState)
    turns: list[Turn] = []


class ConversationStore:
    def __init__(self, max_conversations: int = 200):
        self.max = max_conversations
        self._items: OrderedDict[str, Conversation] = OrderedDict()

    def get(self, conversation_id: str) -> Conversation | None:
        conv = self._items.get(conversation_id)
        if conv:
            self._items.move_to_end(conversation_id)
        return conv

    def get_or_create(self, conversation_id: str | None) -> Conversation:
        """Unknown ids (e.g. after a restart) start a fresh conversation with a new server-issued id."""
        existing = self.get(conversation_id) if conversation_id else None
        if existing:
            return existing
        conv = Conversation(conversation_id=f"conv-{uuid.uuid4().hex[:12]}", created_at=datetime.now(UTC))
        self._items[conv.conversation_id] = conv
        while len(self._items) > self.max:
            self._items.popitem(last=False)
        return conv
