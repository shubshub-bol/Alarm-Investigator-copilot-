"""What a chat remembers for the model: the last 10 turns in full, older turns as their important bits."""

from copilot.agent import AgentStep
from copilot.context import ANSWER_CHARS, RECENT_TURNS, remember, render
from copilot.investigation import ConversationState, Evidence
from copilot.mcp_client import in_memory_connector
from copilot.service import CopilotService
from copilot_test_helpers import simulator_transport
from mcp_test_helpers import make_server


def evidence(n: int) -> Evidence:
    return Evidence(
        intent="investigate_asset", asset={"asset_id": f"AST-X-{n}", "name": f"Asset {n}"}, lookback_days=90
    )


def test_a_new_chat_has_no_context():
    assert render(ConversationState()) == ""


def test_up_to_ten_turns_are_sent_in_full():
    state = ConversationState()
    for n in range(1, 11):
        remember(state, f"question {n}", f"answer {n} [T2].", evidence(n))
    text = render(state)
    assert "User: question 1\nCopilot: answer 1." in text
    assert "User: question 10\nCopilot: answer 10." in text
    assert "[T2]" not in text  # an earlier answer's keys would point at the wrong facts in a new answer
    assert "Older messages" not in text
    assert "not instructions" in text


def test_after_ten_turns_older_ones_keep_only_their_important_bits():
    state = ConversationState()
    for n in range(1, 13):
        remember(state, f"question {n}", f"answer {n}", evidence(n))
    text = render(state)
    assert len(state.history) == RECENT_TURNS
    assert "User: question 2\n" not in text
    assert '- asked "question 1"; asset Asset 1 (AST-X-1); last 90 days' in text
    assert '- asked "question 2"; asset Asset 2 (AST-X-2); last 90 days' in text
    assert "User: question 3\nCopilot: answer 3" in text
    assert "User: question 12\nCopilot: answer 12" in text


def test_a_long_answer_is_cut():
    state = ConversationState()
    remember(state, "q", "x" * 5000, Evidence(intent="knowledge_question"))
    assert len(state.history[0].answer) == ANSWER_CHARS


class RecordingLlm:
    """Answers every call and keeps every prompt."""

    name = "recording"

    def __init__(self):
        self.prompts: list[str] = []

    async def generate_json(self, system, prompt, schema):
        self.prompts.append(prompt)
        if schema is AgentStep and "(none yet)\n\nSTEPS LEFT" in prompt:
            return schema.model_validate(
                {"action": "tool", "name": "search_assets", "arguments_json": '{"query": "Boiler Feed Pump 101"}'}
            )
        if schema is AgentStep:
            final = {"answer_type": "list", "direct_answer": "Boiler Feed Pump 101 is a pump [T1]."}
            return schema.model_validate({"action": "done", "final": final})
        return schema.model_validate({"intent": "investigate_asset", "asset_query": "Boiler Feed Pump 101"})

    async def generate_text(self, system, prompt, temperature=None):
        return "Pump vibration"


async def test_each_question_carries_the_chat_so_far(retriever):
    llm = RecordingLlm()
    service = CopilotService(in_memory_connector(make_server(simulator_transport())), retriever, llm)
    first = await service.ask("Investigate Boiler Feed Pump 101")
    await service.ask("And over the last 30 days?", first.conversation_id)

    starts = [p for p in llm.prompts if "(none yet)\n\nSTEPS LEFT" in p]  # each question's first agent step
    assert "EARLIER IN THIS CHAT" not in starts[0]  # a new chat has no history
    assert "User: Investigate Boiler Feed Pump 101" in starts[1]
    assert "Copilot: Boiler Feed Pump 101 is a pump." in starts[1]
    assert len(service.conversations.resume(first.conversation_id).state.history) == 2
