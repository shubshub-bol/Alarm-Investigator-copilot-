"""Chat titles: LLM reply cleaning, rule-based titles per intent, and how the service picks one."""

import asyncio

import pytest

from copilot.conversations import ConversationStore
from copilot.investigation import Evidence
from copilot.llm import LlmError
from copilot.mcp_client import in_memory_connector
from copilot.service import CopilotService
from copilot.titles import TITLE_SYSTEM, clean_title, rule_title
from copilot.understanding import QueryUnderstanding
from copilot_test_helpers import simulator_transport
from mcp_test_helpers import make_server

ACCEPTANCE = (
    "Investigate recurring high-severity alarms for Boiler Feed Pump 101 over the last 90 days, identify likely "
    "contributing factors, retrieve the relevant operating procedure, and provide recommended actions."
)
RULE_TITLE = "Boiler Feed Pump 101: High Vibration"
BFP101 = {"asset_id": "AST-BFP-101", "name": "Boiler Feed Pump 101"}
VIBRATION_102 = {"alarm_name": "High Vibration", "asset_name": "Boiler Feed Pump 102"}


def understanding(intent: str, **fields) -> QueryUnderstanding:
    return QueryUnderstanding(intent=intent, **fields)


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("BFP-102 Critical Alarm Response", "BFP-102 Critical Alarm Response"),
        ('"P1 Priority Score Meaning."', "P1 Priority Score Meaning"),
        ("Title: Compressor Discharge Pressure\nsecond line", "Compressor Discharge Pressure"),
        ("**EastRefinery Top Alarm**", "EastRefinery Top Alarm"),
        ("Alarms", None),
        ("A title that is far too long to be a sensible chat name for anyone", None),
        ("", None),
    ],
)
def test_llm_replies_are_cleaned_or_rejected(reply, expected):
    assert clean_title(reply) == expected


@pytest.mark.parametrize(
    ("u", "evidence", "documents", "expected"),
    [
        (
            understanding("investigate_asset"),
            Evidence(intent="investigate_asset", asset=BFP101, top_alarm={"alarm_name": "High Vibration"}),
            [],
            "Boiler Feed Pump 101: High Vibration",
        ),
        (
            understanding("investigate_asset"),
            Evidence(intent="investigate_asset", asset=BFP101),
            [],
            "Boiler Feed Pump 101: Alarm Investigation",
        ),
        (
            understanding("active_alarms"),
            Evidence(intent="active_alarms", focus_alarm=VIBRATION_102),
            [],
            "Boiler Feed Pump 102: Active Alarms",
        ),
        (
            understanding("highest_priority", site="EastRefinery"),
            Evidence(intent="highest_priority"),
            [],
            "EastRefinery: Highest Priority Alarm",
        ),
        (
            understanding("highest_priority", site="EastRefinery", unit="Unit 3"),
            Evidence(intent="highest_priority"),
            [],
            "EastRefinery Unit 3: Highest Priority Alarm",
        ),
        (
            understanding("procedure_lookup"),
            Evidence(intent="procedure_lookup", focus_alarm=VIBRATION_102),
            [],
            "Boiler Feed Pump 102: High Vibration Procedure",
        ),
        (
            understanding("related_assets"),
            Evidence(intent="related_assets", focus_alarm=VIBRATION_102),
            [],
            "Boiler Feed Pump 102: Related Assets",
        ),
        (
            understanding("recommendation_check"),
            Evidence(intent="recommendation_check", asset=BFP101),
            [],
            "Boiler Feed Pump 101: Advice Check",
        ),
        (
            understanding("knowledge_question", alarm_topic="motor trip"),
            Evidence(intent="knowledge_question"),
            [],
            "Motor Trip",
        ),
        (
            understanding("knowledge_question"),
            Evidence(intent="knowledge_question"),
            ["Alarm Management Philosophy", "Something Else"],
            "Alarm Management Philosophy",
        ),
    ],
)
def test_rule_titles_name_the_subject_and_topic(u, evidence, documents, expected):
    assert rule_title(u, evidence, documents) == expected


class TitleLlm:
    """Writes titles only: every other call fails, so understanding, answers and small talk use rules and templates."""

    name = "fake"

    def __init__(self, reply: str = "BFP-101 Recurring Vibration", delay: float = 0.0, fail: bool = False):
        self.reply, self.delay, self.fail = reply, delay, fail
        self.title_calls = 0

    async def generate_json(self, system, prompt, schema):
        raise LlmError("structured output is not used in these tests")

    async def generate_text(self, system, prompt, temperature=None):
        if system != TITLE_SYSTEM:
            raise LlmError("only titles are written in these tests")
        self.title_calls += 1
        await asyncio.sleep(self.delay)
        if self.fail:
            raise LlmError("429 RESOURCE_EXHAUSTED")
        return self.reply


def service(retriever, llm, store: ConversationStore) -> CopilotService:
    return CopilotService(in_memory_connector(make_server(simulator_transport())), retriever, llm, conversations=store)


async def test_small_talk_waits_and_the_first_real_question_names_the_chat(retriever):
    store = ConversationStore()
    llm = TitleLlm()
    first = await service(retriever, llm, store).ask("hello")
    assert store.get(first.conversation_id).title == "New chat"
    assert llm.title_calls == 0
    await service(retriever, llm, store).ask(ACCEPTANCE, first.conversation_id)
    assert store.get(first.conversation_id).title == "BFP-101 Recurring Vibration"


async def test_follow_ups_do_not_rename_the_chat(retriever):
    store = ConversationStore()
    llm = TitleLlm()
    first = await service(retriever, llm, store).ask(ACCEPTANCE)
    await service(retriever, llm, store).ask("Which operating procedure applies to this alarm?", first.conversation_id)
    assert llm.title_calls == 1
    assert store.get(first.conversation_id).title == "BFP-101 Recurring Vibration"


async def test_offline_titles_come_from_the_rules(retriever):
    store = ConversationStore()
    response = await service(retriever, None, store).ask(ACCEPTANCE)
    assert store.get(response.conversation_id).title == RULE_TITLE


@pytest.mark.parametrize(
    "llm", [TitleLlm(fail=True), TitleLlm(reply="Alarms"), TitleLlm(delay=5)], ids=["fails", "unusable", "too-slow"]
)
async def test_rules_name_the_chat_when_the_llm_title_does_not_work(retriever, llm, monkeypatch):
    monkeypatch.setattr("copilot.service.TITLE_WAIT_SECONDS", 0.05)
    store = ConversationStore()
    response = await service(retriever, llm, store).ask(ACCEPTANCE)
    assert store.get(response.conversation_id).title == RULE_TITLE
