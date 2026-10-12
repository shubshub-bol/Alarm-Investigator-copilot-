"""Compare the Alarm API's recommended actions with the retrieved document guidance.

Rule-based check used offline and as a safety net next to the LLM's own assessment:
- conflict: the API says to keep operating / monitor for hours, while a document sentence says to stop,
  change over or not continue.
- consistent: a document sentence covers the same specific things (e.g. 'drive motor bearing temperature').
- not_covered: nothing in the retrieved documents addresses the action.
"""

import re
from typing import Literal

from pydantic import BaseModel

from rag.retrieval import RetrievedPassage

Verdict = Literal["consistent", "conflict", "not_covered"]


class ActionAssessment(BaseModel):
    step: int
    action: str
    verdict: Verdict
    document_guidance: str | None = None
    citation_keys: list[str] = []
    explanation: str = ""


_CONTINUE = re.compile(
    r"\b(continue (operation|operating|running)|keep (running|operating|it in service)|remain in service|"
    r"monitor\b.*\b\d+\s*hours?)",
    re.I,
)
_STOP = re.compile(
    r"\b(do not (keep|continue|run|operate)|must not (exceed|continue)|stop the affected|"
    r"change ?over to the standby|start the standby|transfer the load|rather than running|shut ?down)",
    re.I,
)
_GOVERNS = re.compile(r"\b(procedure governs|takes precedence|overrides? (the )?recommendation)", re.I)
_SENTENCE = re.compile(r"(?<=[.!?])(?<!\be\.g\.)(?<!\bi\.e\.)\s+(?=[A-Z0-9])")  # "e.g. C-502" stays one sentence
_WORD = re.compile(r"[a-z][a-z0-9-]+")
_GENERIC = set(
    "check verify schedule plan monitor review confirm inspect next window alarm alarms the and for with at "
    "from into this that then when operation operations operator maintenance level levels value trend".split()
)


def sentences(text: str) -> list[str]:
    flat = " ".join(text.split())
    return [s.strip() for s in _SENTENCE.split(flat) if len(s.strip()) > 12]


def _terms(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) > 3 and w not in _GENERIC}


def governance_keys(passages: list[RetrievedPassage]) -> list[str]:
    return [p.citation.key for p in passages if any(_GOVERNS.search(s) for s in sentences(p.text))]


def assess_actions(actions: list[dict], passages: list[RetrievedPassage]) -> list[ActionAssessment]:
    procedures_first = sorted(passages, key=lambda p: p.citation.doc_type != "operating_procedure")
    governs = governance_keys(passages)
    results = []
    for item in actions:
        action, step = item["action"], item["step"]
        if _CONTINUE.search(action):
            hits = [(p, s) for p in procedures_first for s in sentences(p.text) if _STOP.search(s)]
            if hits:
                keys = list(dict.fromkeys(p.citation.key for p, _ in hits))[:3]
                guidance = " ".join(dict.fromkeys(s for _, s in hits[:2]))
                results.append(
                    ActionAssessment(
                        step=step,
                        action=action,
                        verdict="conflict",
                        document_guidance=guidance,
                        citation_keys=keys + [k for k in governs if k not in keys],
                        explanation="The API suggests continuing to operate, but the documents require stopping "
                        "or changing over. The approved procedure governs.",
                    )
                )
                continue
        wanted = _terms(action)
        best: tuple[int, RetrievedPassage, str] | None = None
        for p in procedures_first:
            for s in sentences(p.text):
                overlap = len(wanted & _terms(s))
                if overlap and (best is None or overlap > best[0]):
                    best = (overlap, p, s)
        if best and best[0] >= min(2, len(wanted)):
            results.append(
                ActionAssessment(
                    step=step,
                    action=action,
                    verdict="consistent",
                    document_guidance=best[2],
                    citation_keys=[best[1].citation.key],
                    explanation=f"{best[1].citation.label} covers the same check.",
                )
            )
        else:
            results.append(
                ActionAssessment(
                    step=step,
                    action=action,
                    verdict="not_covered",
                    explanation="No retrieved document addresses this action; verify before acting.",
                )
            )
    return results
