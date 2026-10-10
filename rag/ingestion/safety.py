"""Prompt-injection screening and text sanitising for retrieved documents.

Documents are untrusted input. A section is flagged (and kept out of answers) when it contains one
strong signal - text that tries to instruct the model - or two weak signals together. A single weak
signal such as a plain mention of 'AI assistants' is normal in procedures and is not flagged.
"""

import re

STRONG = {
    "ignore_instructions": r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}?"
    r"\b(previous|prior|above|earlier|all|your)\b[^.\n]{0,30}?"
    r"\b(instructions?|rules|prompts?|guidelines)\b",
    "system_prompt": r"\b(system|developer)\s+(prompt|message)\b|\breveal\s+(your|the)\s+(prompt|instructions)\b",
    "role_override": r"\b(you are now|from now on,? you|act as an?|pretend to be)\b",
    "role_marker": r"(^|\n|<!--)\s*(assistant|system|user)\s*:",
    "tool_coercion": r"\b(call|invoke|run|execute)\s+(every|all|any)\s+(available\s+)?tools?\b",
}
WEAK = {
    "addressed_to_ai": r"\b(ai assistants?|language models?|llms?|chatbots?)\b",
    "secrecy": r"\bdo not (mention|reveal|cite|disclose)\b",
    "unsafe_bypass": r"\b(safe|ok|okay|fine|allowed) to bypass\b",
}
_STRONG = {k: re.compile(v, re.IGNORECASE) for k, v in STRONG.items()}
_WEAK = {k: re.compile(v, re.IGNORECASE) for k, v in WEAK.items()}

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_INVISIBLE = re.compile("[​-‏⁠﻿‪-‮]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACES = re.compile(r"[ \t]{2,}")


def screen_for_injection(raw_text: str) -> tuple[bool, list[str]]:
    """Check RAW text (before sanitising, so hidden comments are seen). Returns (flagged, reasons)."""
    strong = [name for name, rx in _STRONG.items() if rx.search(raw_text)]
    weak = [name for name, rx in _WEAK.items() if rx.search(raw_text)]
    flagged = bool(strong) or len(weak) >= 2
    return flagged, sorted(strong + weak) if flagged else []


def sanitize(text: str) -> str:
    """Remove content a reader cannot see but a model would: HTML comments, zero-width and control characters."""
    text = _INVISIBLE.sub("", text)
    text = _HTML_COMMENT.sub("", text)
    text = _CONTROL.sub("", text)
    text = _SPACES.sub(" ", text)
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()
