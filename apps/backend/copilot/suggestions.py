"""Questions the GUI offers: starters for an empty conversation, follow-ups after an answer."""

from copilot.investigation import Evidence

# Standalone questions only: each makes sense as the first question of a conversation.
STARTER_QUESTIONS = [
    "Investigate recurring high-severity alarms for Boiler Feed Pump 101 over the last 90 days, identify likely "
    "contributing factors, retrieve the relevant operating procedure, and provide recommended actions with source "
    "evidence.",
    "Show active critical alarms for Boiler Feed Pump 102 and recommend immediate actions.",
    "Why are compressor discharge pressure alarms repeatedly occurring?",
    "Which alarm has the highest priority in EastRefinery, and why?",
]


def follow_ups(evidence: Evidence) -> list[str]:
    """Next questions that build on this answer (they rely on the conversation context)."""
    out: list[str] = []
    has_alarm = evidence.focus_alarm is not None or evidence.top_alarm is not None
    if has_alarm and evidence.intent != "procedure_lookup":
        out.append("Which operating procedure applies to this alarm?")
    if evidence.recommendations and evidence.intent != "recommendation_check":
        out.append("Are the API recommendations consistent with the maintenance manual?")
    if has_alarm and evidence.intent != "related_assets":
        out.append("What related assets should be inspected for this alarm?")
    if evidence.asset and evidence.intent not in ("active_alarms", "highest_priority"):
        out.append(f"Are there active alarms on {evidence.asset['name']} right now?")
    return out[:4] or STARTER_QUESTIONS[1:4]
