"""Chat titles. The first real question names a chat once: a short LLM title, or a rule-based one offline.

Titles are plain text. The rules build "{subject}: {topic}" from what the copilot understood and found, so a
title is never just the question cut short.
"""

import logging
import re

from copilot.investigation import Evidence
from copilot.llm import LlmProvider
from copilot.understanding import QueryUnderstanding

log = logging.getLogger("copilot.titles")

MAX_LLM_TITLE = 60
TITLE_SYSTEM = (
    "You name conversations in a plant alarm investigation tool. Reply with only a title of 2 to 6 words in Title "
    "Case that names the asset, site or topic. No quotes, no trailing punctuation."
)
_FIXED_TOPICS = {
    "active_alarms": "Active Alarms",
    "highest_priority": "Highest Priority Alarm",
    "related_assets": "Related Assets",
    "recommendation_check": "Advice Check",
}


def clean_title(reply: str) -> str | None:
    """The model's reply as a title, or None when it is not a short one-line title."""
    lines = reply.strip().splitlines()
    if not lines:
        return None
    title = re.sub(r"^title\s*:\s*", "", lines[0].strip(), flags=re.IGNORECASE)
    title = title.strip("\"'`*# “”‘’")
    title = re.sub(r"[.!?;:,]+$", "", title).strip()
    title = re.sub(r"\s+", " ", title)
    if not 2 <= len(title.split(" ")) <= 8 or len(title) > MAX_LLM_TITLE:
        return None
    return title


async def llm_title(llm: LlmProvider, question: str) -> str | None:
    """A short title from the language model, or None (the caller then uses the rules)."""
    try:
        reply = await llm.generate_text(TITLE_SYSTEM, f"First question: {question}")
    except Exception as exc:  # noqa: BLE001 - a missing title must never break an answer
        log.warning("llm title failed; using rules", extra={"fields": {"reason": str(exc)[:200]}})
        return None
    title = clean_title(reply)
    if title is None:
        log.warning("llm title unusable; using rules", extra={"fields": {"reply": reply[:80]}})
    return title


def rule_title(u: QueryUnderstanding, evidence: Evidence, documents: list[str]) -> str:
    """'{subject}: {topic}', or the topic alone. `documents` are the cited document titles, in citation order."""
    focus = evidence.focus_alarm or {}
    alarm_name = focus.get("alarm_name") or (evidence.top_alarm or {}).get("alarm_name")
    subject = (
        focus.get("asset_name") or (evidence.asset or {}).get("name") or " ".join(p for p in (u.site, u.unit) if p)
    )
    if u.intent == "investigate_asset":
        topic = alarm_name or "Alarm Investigation"
    elif u.intent == "procedure_lookup":
        topic = f"{alarm_name} Procedure" if alarm_name else "Operating Procedure"
    elif u.intent == "knowledge_question":
        topic = _capitalised(u.alarm_topic) if u.alarm_topic else (documents[0] if documents else "Plant Documents")
    else:
        topic = _FIXED_TOPICS.get(u.intent, "Alarm Question")
    return f"{subject}: {topic}" if subject else topic


def _capitalised(text: str) -> str:
    return " ".join(word[:1].upper() + word[1:] for word in text.split())
