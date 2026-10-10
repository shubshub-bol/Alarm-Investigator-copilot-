"""Replies to small talk and off-topic messages: written by the language model, with the fixed text as fallback."""

import pytest
from fastapi.testclient import TestClient

from copilot.answer import CAPABILITIES, CONVERSATION_SYSTEM, conversation_reply
from copilot.api import create_app
from copilot.config import CopilotSettings
from copilot.llm import LlmError
from copilot.mcp_client import in_memory_connector
from copilot.service import CopilotService
from copilot_test_helpers import simulator_transport
from mcp_test_helpers import make_server


class ChattyLlm:
    """Writes conversational replies; structured calls fail, so nothing else in a request needs a model."""

    name = "fake"

    def __init__(self, reply: str = "Hi! Ask me about an alarm, an asset or a site.", fail: bool = False):
        self.reply, self.fail = reply, fail
        self.calls: list[tuple[str, str]] = []

    async def generate_json(self, system, prompt, schema):
        raise LlmError("structured output is not used in these tests")

    async def generate_text(self, system, prompt):
        self.calls.append((system, prompt))
        if self.fail:
            raise LlmError("429 RESOURCE_EXHAUSTED")
        return self.reply


async def test_the_language_model_writes_a_reply_that_fits_the_message():
    llm = ChattyLlm("Jokes aren't my thing.\n Want to see the highest-priority alarm in EastRefinery?")
    answer = await conversation_reply("tell me a joke", llm)
    assert answer.summary == "Jokes aren't my thing. Want to see the highest-priority alarm in EastRefinery?"
    assert answer.grounding.mode == "llm"
    assert answer.citations == [] and answer.suggestions
    system, prompt = llm.calls[0]
    assert system == CONVERSATION_SYSTEM and "tell me a joke" in prompt


@pytest.mark.parametrize(
    "llm",
    [None, ChattyLlm(fail=True), ChattyLlm(reply="   "), ChattyLlm(reply="word " * 200)],
    ids=["offline", "llm-fails", "empty-reply", "too-long"],
)
async def test_the_fixed_text_is_the_fallback(llm):
    answer = await conversation_reply("what can you do?", llm)
    assert answer.summary == CAPABILITIES
    assert answer.grounding.mode == "template"


async def test_offline_thanks_still_gets_its_own_short_reply():
    assert (await conversation_reply("thanks", None)).summary.startswith("You're welcome")


def test_a_greeting_gets_the_models_reply_and_never_touches_the_alarm_api(retriever):
    transport = simulator_transport()
    service = CopilotService(in_memory_connector(make_server(transport)), retriever, ChattyLlm())
    with TestClient(create_app(CopilotSettings(_env_file=None), service=service)) as api:
        body = api.post("/api/chat", json={"message": "hello"}).json()
    assert body["answer"]["summary"] == "Hi! Ask me about an alarm, an asset or a site."
    assert body["status"] == "ok"
    assert body["trace"] == [] and body["discovery"] is None
    assert transport.requests == []  # no alarm tools, no documents
