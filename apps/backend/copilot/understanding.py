"""Turn a question into an intent plus entities (asset, site, unit, time window, severity, alarm topic).

The keyword rules read every question: they recognise small talk, and without a language model their reading picks
the playbook. With a model, the agent's recipes are the model's reading; merge_with_rules checks and completes each
one with the rules' entities.
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PlaybookIntent = Literal[
    "investigate_asset",  # why is this asset alarming, contributing factors, procedure, actions
    "active_alarms",  # what is active now on an asset/site and what to do immediately
    "highest_priority",  # which alarm in a site/unit matters most and why
    "related_assets",  # which connected assets to inspect for an alarm
    "procedure_lookup",  # which procedure applies to an alarm
    "recommendation_check",  # do the API recommendations agree with the documents
    "knowledge_question",  # general documentation question, no plant data needed
    "conversation",  # greetings, thanks, "what can you do": no tools, no documents
]
Intent = Literal[
    PlaybookIntent,
    "agent",  # answered by the agent's own tool steps rather than one playbook
]
Severity = Literal["low", "medium", "high", "critical"]


class _Reading[IntentT: str](BaseModel):
    """What a reading of the question holds. The intent type is a parameter, so the model's reading and the
    copilot's own can allow different intents without a subclass widening its parent's field."""

    model_config = ConfigDict(extra="ignore")

    intent: IntentT
    asset_query: str | None = Field(None, max_length=80, description="Asset name or id exactly as the user wrote it")
    asset_type: str | None = Field(None, max_length=30, description="pump, motor, compressor... if no specific asset")
    site: str | None = Field(None, max_length=40, description="Site name without spaces, e.g. EastRefinery")
    unit: str | None = Field(None, max_length=20, description="e.g. 'Unit 3'")
    lookback_days: int | None = Field(None, ge=1, le=365, description="Only if the user gave a time window")
    severities: list[Severity] | None = None
    status: Literal["active"] | None = None
    alarm_topic: str | None = Field(None, max_length=40, description="e.g. 'vibration', 'motor trip'")
    uses_context: bool = Field(False, description="True if the question refers to 'this alarm' or the previous answer")
    rationale: str = Field("", max_length=300)


# A model's reading of the question: the inputs of one of the agent's recipes. Its intent is never "agent".
class LlmUnderstanding(_Reading[PlaybookIntent]):
    """A model's reading: checks a recipe's inputs and describes them on the agent's menu."""


# The copilot's reading of a question: the rules', the model's merged with the rules', or the agent's ("agent").
class QueryUnderstanding(_Reading[Intent]):
    model_config = ConfigDict(extra="forbid")

    question: str = ""  # the original text, kept for document search
    method: Literal["rules", "llm"] = "rules"
    fallback_reason: str | None = None
    rules_intent: Intent | None = None  # the keyword rules' intent when the model decided


# --------------------------------------------------------------------------- rules
_EQUIPMENT = r"pump|motor|compressor|cooler|valve|deaerator|fan|boiler|heater|exchanger"
_MODIFIERS = (
    r"boiler|feed|feedwater|gas|recycle|discharge|crude|charge|condensate|cooling|water|instrument|air|"
    r"forced|draft|fired|drive|control"
)
_NAMED_ASSET = re.compile(rf"\b((?:(?:{_MODIFIERS})\s+){{0,3}})({_EQUIPMENT})\s+([a-z]{{0,3}}-?\d{{2,3}})\b", re.I)
_ASSET_ID = re.compile(r"\b(AST-[A-Z]{2,4}-\d{3}|[A-Z]{1,4}-\d{3})\b")
_EQUIPMENT_PHRASE = re.compile(rf"\b(?:(?:{_MODIFIERS})\s+){{0,3}}({_EQUIPMENT})s?\b", re.I)  # boiler feed pump: pump
_SITE = re.compile(r"\b(east|north|south|west)\s?(refinery|plant)\b", re.I)
_UNIT = re.compile(r"\bunit\s*(\d{1,2})\b", re.I)
_ALARM_TOPICS = [
    "discharge pressure", "suction pressure", "bearing temperature", "winding temperature", "outlet temperature",
    "differential pressure", "motor trip", "seal leak", "position deviation", "flame failure", "drum level",
    "vibration", "surge", "overload", "trip", "level",
]  # fmt: skip
_DAYS = {"day": 1, "week": 7, "month": 30, "quarter": 90, "year": 365}
_N_UNITS = re.compile(r"\b(?:last|past|previous)\s+(\d{1,3})\s*(day|week|month|year)s?\b", re.I)
_ONE_UNIT = re.compile(r"\b(?:last|past|previous)\s+(day|week|month|quarter|year)\b", re.I)
_BARE_DAYS = re.compile(r"\b(\d{1,3})\s*days?\b", re.I)
_ACTIVE = re.compile(r"\b(active|current(ly)?|right now|ongoing|standing|still on)\b", re.I)
_CONTEXT = re.compile(  # "this alarm", "this high-severity motor trip alarm", "that one"
    r"\b(this|that|these|those|the same)\s+(?:[\w-]+\s+){0,3}?"
    r"(alarms?|assets?|pumps?|motors?|compressors?|one|issue)\b",
    re.I,
)

_INTENT_RULES: list[tuple[Intent, re.Pattern]] = [
    ("recommendation_check", re.compile(
        r"\b(consistent|consistency|agree|agreement|conflicts?|contradict\w*|in line with)\b|"
        r"\bmatch(es)?\s+(the|our)\s+(manual|procedure|sop)", re.I)),
    ("highest_priority", re.compile(
        r"\b(highest|top|most)[\s-]+(priority|urgent|important|critical)\b|\bprioriti[sz]e\b|\brank\b", re.I)),
    ("related_assets", re.compile(
        r"\brelated\s+(assets?|equipment)\b|\bshould be inspected\b|"
        r"\b(other|else|nearby|connected|upstream|downstream)\b.*\b(inspect|check)", re.I)),
    ("investigate_asset", re.compile(
        r"\b(investigate|investigation|why|recurring|recur|repeated(ly)?|root cause|contributing|diagnos\w*)\b|"
        r"\bkeeps?\s+(happening|occurring|coming)|\bwhat('s| is) wrong\b", re.I)),
]  # fmt: skip
_PROCEDURE = re.compile(r"\b(procedure|sop|manual|guide|instructions?|steps)\b", re.I)
_SMALL_TALK = re.compile(
    r"^(hi|hello|hey|hiya|good (morning|afternoon|evening)|thanks|thank you|cheers|ok|okay|great|cool|bye|"
    r"goodbye|help|what can you do|who are you|what are you|how does this work)\b",
    re.I,
)


def _asset_query(text: str) -> str | None:
    match = _NAMED_ASSET.search(text)
    if match:
        words = [w.title() for w in match.group(1).split()] + [match.group(2).title(), match.group(3).upper()]
        return " ".join(words)
    match = _ASSET_ID.search(text)
    return match.group(1) if match else None


def _lookback_days(text: str) -> int | None:
    if m := _N_UNITS.search(text):
        return min(int(m.group(1)) * _DAYS[m.group(2).lower()], 365)
    if m := _ONE_UNIT.search(text):
        return _DAYS[m.group(1).lower()]
    if m := _BARE_DAYS.search(text):
        return min(int(m.group(1)), 365)
    return None


def _severities(text: str) -> list[str] | None:
    lower = text.lower()
    if re.search(r"high[\s-]severity|high and critical|high/critical", lower):
        return ["high", "critical"]
    if re.search(r"\bcritical\b", lower):
        return ["critical"]
    return None


def understand_with_rules(question: str) -> QueryUnderstanding:
    asset_query = _asset_query(question)
    equipment = _EQUIPMENT_PHRASE.search(question)
    asset_type = equipment.group(1).lower() if equipment else None
    site_match, unit_match = _SITE.search(question), _UNIT.search(question)
    lower = question.lower()
    topic = next((t for t in _ALARM_TOPICS if re.search(rf"\b{t}\b", lower)), None)
    status = "active" if _ACTIVE.search(question) else None
    uses_context = bool(_CONTEXT.search(question))

    intent: Intent | None = next((name for name, rx in _INTENT_RULES if rx.search(question)), None)
    has_subject = bool(asset_query or asset_type or site_match or unit_match)
    plain = " ".join(re.sub(r"[^\w\s']", " ", question).split())
    if _SMALL_TALK.match(plain) and len(plain.split()) <= 6 and not (has_subject or topic or intent):
        intent = "conversation"  # e.g. "hello", "thanks", "what can you do?"
    if intent is None:
        if status and has_subject:
            intent = "active_alarms"
        elif _PROCEDURE.search(question) and (asset_query or topic or uses_context):
            intent = "procedure_lookup"
        elif asset_query or asset_type:
            intent = "investigate_asset"
        elif site_match or unit_match:
            intent = "active_alarms"
        else:
            intent = "knowledge_question"
    if intent in ("procedure_lookup", "recommendation_check") and not asset_query:
        uses_context = True  # "the alarm" / "the API recommendations" means the one we just discussed

    return QueryUnderstanding(
        intent=intent,
        asset_query=asset_query,
        asset_type=asset_type,
        site=f"{site_match.group(1).title()}{site_match.group(2).title()}" if site_match else None,
        unit=f"Unit {int(unit_match.group(1))}" if unit_match else None,
        lookback_days=_lookback_days(question),
        severities=_severities(question),
        status=status,
        alarm_topic=topic,
        uses_context=uses_context,
        rationale="keyword rules",
        question=question,
        method="rules",
    )


# ------------------------------------------------------------- a model's reading
def without_spaces(site: str) -> str:
    """A site as the Alarm API names it: 'East Refinery' -> 'EastRefinery'."""
    return "".join(site.split())


def _type_name(word: str) -> str:
    """'Boilers' -> 'boiler', 'heat exchangers' -> 'heat_exchanger': the form asset types are stored in."""
    word = "_".join(word.lower().split())
    return word[:-1] if word.endswith("s") and not word.endswith("ss") else word


def merge_with_rules(parsed: LlmUnderstanding, rules: QueryUnderstanding, question: str) -> QueryUnderstanding:
    """A model's reading of the question (an agent's recipe), completed and checked by the keyword rules."""
    # The model decides the intent; precise rule-extracted entities fill anything it left empty.
    merged = parsed.model_dump()
    for name in _GAP_FILL_FIELDS:
        if merged[name] in (None, []) and getattr(rules, name) not in (None, []):
            merged[name] = getattr(rules, name)
    merged["severities"] = at_or_above(merged["severities"])
    merged["uses_context"] = merged["uses_context"] or rules.uses_context
    if merged["site"]:  # the Alarm API matches a site ignoring case, not spaces: "East Refinery" finds nothing
        merged["site"] = without_spaces(merged["site"])
    # A type word is no asset name. Seen with an OpenRouter model: "any boiler alarms active" with the asset
    # 'boiler', which matches three asset names equally, two of them boiler feed pumps.
    if merged["asset_query"] and not re.search(r"\d", merged["asset_query"]):
        merged["asset_type"] = merged["asset_type"] or merged["asset_query"]
        merged["asset_query"] = None
    if merged["asset_type"]:
        merged["asset_type"] = _type_name(merged["asset_type"])
    return QueryUnderstanding(**merged, question=question, method="llm", rules_intent=rules.intent)


_GAP_FILL_FIELDS = ("asset_type", "site", "unit", "lookback_days", "severities", "alarm_topic", "status")
_ORDER = ["low", "medium", "high", "critical"]


def at_or_above(severities: list[str] | None) -> list[str] | None:
    """Severity filters mean 'this level or worse': 'high' alarms include critical ones."""
    if not severities:
        return severities
    return _ORDER[min(_ORDER.index(s) for s in severities) :]
