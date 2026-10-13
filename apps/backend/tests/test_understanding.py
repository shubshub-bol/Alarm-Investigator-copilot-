"""Intent and entity extraction by the keyword rules, and how a model's reading is merged with them."""

import pytest
from pydantic import ValidationError

from copilot.understanding import (
    LlmUnderstanding,
    QueryUnderstanding,
    merge_with_rules,
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


def test_general_document_question_needs_no_tools():
    u = understand_with_rules("What does the safety instruction say about lockout?")
    assert u.intent == "knowledge_question"
    assert u.asset_query is None


def test_named_asset_alone_defaults_to_investigation():
    assert understand_with_rules("Boiler Feed Pump 101").intent == "investigate_asset"


# ------------------------------------------------- a model's reading, merged with the rules
# The agent's recipes are the model's reading of the question; merge_with_rules checks and completes each one.


def merged(question, **reply):
    return merge_with_rules(LlmUnderstanding(**reply), understand_with_rules(question), question)


def test_the_models_reading_is_kept():
    u = merged(ACCEPTANCE, intent="investigate_asset", asset_query="Boiler Feed Pump 101", rationale="why it recurs")
    assert (u.method, u.intent, u.asset_query, u.question) == (
        "llm",
        "investigate_asset",
        "Boiler Feed Pump 101",
        ACCEPTANCE,
    )


def test_rules_fill_entities_the_model_left_empty():
    """Seen with Gemini: it classified the intent right but dropped asset_type and narrowed severity."""
    question = "What related assets should be inspected for this high-severity motor trip alarm?"
    u = merged(question, intent="related_assets", alarm_topic="motor trip", severities=["high"])
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
def test_a_named_asset_always_gets_its_live_data(reply):
    u = merged("Boiler B-201 how many times it failed in past ?", **reply)
    assert u.intent == "investigate_asset"
    assert u.asset_query == "Boiler B-201"


def test_a_document_question_without_an_asset_stays_a_document_question():
    u = merged("What does the safety instruction say about lockout?", intent="knowledge_question")
    assert u.intent == "knowledge_question"


def test_the_rules_intent_is_recorded_when_the_model_decides():
    u = merged("Boiler Feed Pump 101 vibration, what should I check?", intent="related_assets")
    assert u.intent == "related_assets"  # the model decides
    assert u.rules_intent == "investigate_asset"  # and the disagreement stays visible


def test_rules_only_understanding_has_no_second_intent():
    assert understand_with_rules("Boiler Feed Pump 101").rules_intent is None


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
        {"intent": "active_alarms", "asset_query": "Boilers"},
    ],
)
def test_a_type_word_is_not_an_asset_name(reply):
    u = merged("any boiler alarma are active", **reply)
    assert u.asset_query is None  # 'boiler' names no single asset: three asset names contain the word
    assert u.asset_type == "boiler"


def test_a_merged_reading_widens_severities_and_fills_gaps_from_the_rules():
    question = "Investigate Boiler Feed Pump 101 over the last 30 days"
    u = merged(question, intent="investigate_asset", asset_query="Boiler Feed Pump 101", severities=["high"])
    assert (u.severities, u.lookback_days) == (["high", "critical"], 30)


@pytest.mark.parametrize("site", ["East Refinery", " east  refinery ", "EastRefinery"])
def test_a_merged_reading_writes_the_site_without_spaces(site):
    u = merged("Are there active alarms at the east refinery right now?", intent="active_alarms", site=site)
    assert u.site.lower() == "eastrefinery" and " " not in u.site  # the Alarm API ignores case, not spaces


def test_agent_is_an_intent():
    assert QueryUnderstanding(intent="agent").intent == "agent"


def test_the_model_cannot_choose_the_agent_intent():
    with pytest.raises(ValidationError):
        LlmUnderstanding(intent="agent")


def test_a_models_out_of_range_values_are_rejected():
    with pytest.raises(ValidationError):
        LlmUnderstanding(intent="investigate_asset", lookback_days=5000)


def test_understanding_schema_is_strict():
    with pytest.raises(ValueError):
        QueryUnderstanding.model_validate({"intent": "launch_rockets"})
