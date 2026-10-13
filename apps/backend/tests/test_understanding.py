"""Intent and entity extraction: rule-based understanding, and the LLM path with fallback."""

import pytest
from pydantic import ValidationError

from copilot.llm import LlmError
from copilot.understanding import (
    LlmUnderstanding,
    QueryUnderstanding,
    merge_with_rules,
    understand,
    understand_with_rules,
)

ACCEPTANCE = (
    "Investigate recurring high-severity alarms for Boiler Feed Pump 101 over the last 90 days, identify likely "
    "contributing factors, retrieve the relevant operating procedure, and provide recommended actions with source "
    "evidence."
)


# ------------------------------------------------------------- the brief's questions


def test_acceptance_scenario():
    u = understand_with_rules(ACCEPTANCE)
    assert u.intent == "investigate_asset"
    assert u.asset_query == "Boiler Feed Pump 101"
    assert u.lookback_days == 90
    assert u.severities == ["high", "critical"]
    assert u.method == "rules"


def test_active_critical_alarms_with_actions():
    u = understand_with_rules("Show active critical alarms for Boiler Feed Pump 102 and recommend immediate actions.")
    assert u.intent == "active_alarms"
    assert u.asset_query == "Boiler Feed Pump 102"
    assert u.status == "active"
    assert u.severities == ["critical"]


def test_recurring_alarm_topic_without_a_named_asset():
    u = understand_with_rules("Why are compressor discharge pressure alarms repeatedly occurring?")
    assert u.intent == "investigate_asset"
    assert u.asset_query is None
    assert u.asset_type == "compressor"
    assert u.alarm_topic == "discharge pressure"


def test_highest_priority_in_site():
    u = understand_with_rules("Which alarm has the highest priority in EastRefinery, and why?")
    assert u.intent == "highest_priority"
    assert u.site == "EastRefinery"


def test_related_assets_for_this_motor_trip():
    u = understand_with_rules("What related assets should be inspected for this motor trip alarm?")
    assert u.intent == "related_assets"
    assert u.asset_type == "motor"
    assert u.alarm_topic == "motor trip"
    assert u.uses_context is True


def test_procedure_for_this_alarm():
    u = understand_with_rules("Which operating procedure applies to this alarm?")
    assert u.intent == "procedure_lookup"
    assert u.uses_context is True


def test_recommendation_consistency_check():
    u = understand_with_rules("Are the API recommendations consistent with the maintenance manual?")
    assert u.intent == "recommendation_check"
    assert u.uses_context is True


# ------------------------------------------------------------------ entity details


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Investigate Gas Compressor C-301", "Gas Compressor C-301"),
        ("what is wrong with motor M-501?", "Motor M-501"),
        ("check BFP-102 please", "BFP-102"),
        ("alarms on AST-HX-301", "AST-HX-301"),
        ("tell me about cooling water pump 601", "Cooling Water Pump 601"),
    ],
)
def test_named_assets(text, expected):
    assert understand_with_rules(text).asset_query == expected


@pytest.mark.parametrize(
    ("text", "days"),
    [("in the last 2 weeks", 14), ("over the last month", 30), ("past 30 days", 30), ("last quarter", 90)],
)
def test_time_windows(text, days):
    assert understand_with_rules(f"Investigate Boiler Feed Pump 101 {text}").lookback_days == days


def test_site_and_unit_names():
    u = understand_with_rules("Which alarm is most urgent in East Refinery unit 3?")
    assert (u.site, u.unit) == ("EastRefinery", "Unit 3")


@pytest.mark.parametrize(
    "text", ["Which procedure covers this alarm?", "Inspect what for this high-severity motor trip alarm?"]
)
def test_follow_up_references_are_detected(text):
    assert understand_with_rules(text).uses_context is True


@pytest.mark.parametrize(
    "text", ["hello", "Hi there!", "good morning", "thanks", "Thank you very much", "what can you do?", "help", "ok"]
)
def test_small_talk_is_conversation(text):
    assert understand_with_rules(text).intent == "conversation"


@pytest.mark.parametrize(
    "text",
    [
        "hello, why is Boiler Feed Pump 101 alarming?",
        "thanks, now show active alarms in EastRefinery",
        "help with motor trip",
    ],
)
def test_small_talk_words_do_not_hide_a_real_question(text):
    assert understand_with_rules(text).intent != "conversation"


async def test_small_talk_skips_the_llm():
    llm = FakeLlm(error=AssertionError("the LLM must not be called for a greeting"))
    u = await understand("hello", llm)
    assert u.intent == "conversation"
    assert llm.calls == 0


def test_general_document_question_needs_no_tools():
    u = understand_with_rules("What does the safety instruction say about lockout?")
    assert u.intent == "knowledge_question"
    assert u.asset_query is None


def test_named_asset_alone_defaults_to_investigation():
    assert understand_with_rules("Boiler Feed Pump 101").intent == "investigate_asset"


# ------------------------------------------------------------------------ LLM path


class FakeLlm:
    name = "fake"

    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, 0

    async def generate_json(self, system, prompt, schema):
        self.calls += 1
        self.prompt = prompt
        if self.error:
            raise self.error
        return schema.model_validate(self.reply)

    async def generate_text(self, system, prompt):  # pragma: no cover - not used here
        raise NotImplementedError


async def test_llm_understanding_is_used_when_valid():
    reply = {
        "intent": "investigate_asset",
        "asset_query": "Boiler Feed Pump 101",
        "lookback_days": 90,
        "severities": ["high", "critical"],
        "rationale": "asks why alarms recur",
    }
    llm = FakeLlm(reply=reply)
    u = await understand(ACCEPTANCE, llm)
    assert u.method == "llm"
    assert u.asset_query == "Boiler Feed Pump 101"
    assert llm.calls == 1


async def test_rules_fill_entities_the_llm_left_empty():
    """Seen with Gemini: it classified the intent right but dropped asset_type and narrowed severity."""
    question = "What related assets should be inspected for this high-severity motor trip alarm?"
    llm = FakeLlm(reply={"intent": "related_assets", "alarm_topic": "motor trip", "severities": ["high"]})
    u = await understand(question, llm)
    assert u.method == "llm"
    assert u.intent == "related_assets"
    assert u.asset_type == "motor"  # filled from rules
    assert u.severities == ["high", "critical"]  # 'high' means high or worse
    assert u.uses_context is True


@pytest.mark.parametrize(
    "reply",
    [
        {"intent": "knowledge_question", "asset_query": "Boiler B-201"},  # seen with an OpenRouter model
        {"intent": "knowledge_question"},  # the asset is named, but only the rules picked it up
        {"intent": "conversation", "asset_query": "Boiler B-201"},
    ],
)
async def test_a_named_asset_always_gets_its_live_data(reply):
    u = await understand("Boiler B-201 how many times it failed in past ?", FakeLlm(reply=reply))
    assert u.intent == "investigate_asset"
    assert u.asset_query == "Boiler B-201"


async def test_a_document_question_without_an_asset_stays_a_document_question():
    llm = FakeLlm(reply={"intent": "knowledge_question"})
    u = await understand("What does the safety instruction say about lockout?", llm)
    assert u.intent == "knowledge_question"


async def test_the_rules_intent_is_recorded_when_the_model_decides():
    llm = FakeLlm(reply={"intent": "related_assets"})
    u = await understand("Boiler Feed Pump 101 vibration, what should I check?", llm)
    assert u.intent == "related_assets"  # the model decides
    assert u.rules_intent == "investigate_asset"  # and the disagreement stays visible


async def test_rules_only_understanding_has_no_second_intent():
    assert (await understand("Boiler Feed Pump 101", None)).rules_intent is None


@pytest.mark.parametrize(
    ("text", "asset_type"),
    [
        ("Any boiler alarms active?", "boiler"),
        ("Any boiler feed pump alarms active?", "pump"),  # 'boiler' and 'feed' describe the pump
        ("Investigate Boiler Feed Pump 101", "pump"),
        ("Why are compressor discharge pressure alarms repeatedly occurring?", "compressor"),
    ],
)
def test_the_asset_type_is_the_equipment_word_a_phrase_ends_with(text, asset_type):
    assert understand_with_rules(text).asset_type == asset_type


@pytest.mark.parametrize(
    "reply",
    [
        {"intent": "active_alarms", "asset_query": "boiler", "asset_type": "boiler"},  # seen with an OpenRouter model
        {"intent": "active_alarms", "asset_query": "boilers"},
    ],
)
async def test_a_type_word_is_not_an_asset_name(reply):
    u = await understand("any boiler alarma are active", FakeLlm(reply=reply))
    assert u.asset_query is None  # 'boiler' names no single asset: three asset names contain the word
    assert u.asset_type == "boiler"


async def test_the_model_sees_the_earlier_messages():
    llm = FakeLlm(reply={"intent": "investigate_asset", "asset_query": "Boiler Feed Pump 101", "lookback_days": 30})
    context = "\n".join(
        ["Earlier in this chat, oldest first.", "User: Investigate Boiler Feed Pump 101", "Copilot: It vibrates."]
    )
    u = await understand("And over the last 30 days?", llm, context)
    assert "User: Investigate Boiler Feed Pump 101" in llm.prompt
    assert llm.prompt.endswith("Question: And over the last 30 days?")
    assert (u.asset_query, u.lookback_days) == ("Boiler Feed Pump 101", 30)


def test_a_merged_reading_takes_a_type_word_as_a_type():
    question = "any boiler alarms active"
    u = merge_with_rules(
        LlmUnderstanding(intent="active_alarms", asset_query="Boilers"), understand_with_rules(question), question
    )
    assert (u.asset_query, u.asset_type, u.method, u.question) == (None, "boiler", "llm", question)


def test_a_merged_reading_gives_a_named_asset_its_live_data():
    question = "Boiler B-201 how many times it failed in past ?"
    u = merge_with_rules(LlmUnderstanding(intent="knowledge_question"), understand_with_rules(question), question)
    assert (u.intent, u.asset_query) == ("investigate_asset", "Boiler B-201")


def test_a_merged_reading_widens_severities_and_fills_gaps_from_the_rules():
    question = "Investigate Boiler Feed Pump 101 over the last 30 days"
    parsed = LlmUnderstanding(intent="investigate_asset", asset_query="Boiler Feed Pump 101", severities=["high"])
    u = merge_with_rules(parsed, understand_with_rules(question), question)
    assert (u.severities, u.lookback_days) == (["high", "critical"], 30)


def test_agent_is_an_intent():
    assert QueryUnderstanding(intent="agent").intent == "agent"


def test_the_model_cannot_choose_the_agent_intent():
    with pytest.raises(ValidationError):
        LlmUnderstanding(intent="agent")


async def test_llm_failure_falls_back_to_rules():
    u = await understand(ACCEPTANCE, FakeLlm(error=LlmError("quota exceeded")))
    assert u.method == "rules"
    assert u.intent == "investigate_asset"
    assert "quota exceeded" in u.fallback_reason


async def test_llm_out_of_range_values_are_rejected_then_rules_used():
    u = await understand(ACCEPTANCE, FakeLlm(reply={"intent": "investigate_asset", "lookback_days": 5000}))
    assert u.method == "rules"
    assert u.lookback_days == 90


async def test_no_llm_means_rules():
    assert (await understand("Boiler Feed Pump 101", None)).method == "rules"


def test_understanding_schema_is_strict():
    with pytest.raises(ValueError):
        QueryUnderstanding.model_validate({"intent": "launch_rockets"})
