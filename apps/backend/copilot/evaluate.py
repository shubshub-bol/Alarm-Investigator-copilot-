"""Copilot evaluation on a golden question set: each question must get the right kind of investigation, the right
asset, live data when it needs it, the right documents, the key facts, the procedure's step first, and only valid
citations.

    python -m copilot.evaluate                                                    # rules and templates: no LLM
    python -m copilot.evaluate --llm --limit 10 --report docs/evaluation/run.md   # with the model, part 1
    python -m copilot.evaluate --llm --skip 10 --limit 10 --report ...            # part 2, and so on
    --repeat asks every question twice and compares the facts part (doubles the requests)

A question one recipe covers costs about 2 model requests, any other 2 to 6, plus 1 for a new chat's title. It needs
the Alarm API and the MCP server running, as the backend does.
Its chats stay in memory, never in the saved chat history. The test suite runs the same set offline on an
in-memory stack (tests/test_evaluate.py).
"""

import argparse
import asyncio
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from copilot.config import CopilotSettings
from copilot.conversations import ConversationStore
from copilot.llm import build_provider
from copilot.mcp_client import http_connector
from copilot.service import ChatResponse, CopilotService
from copilot.suggestions import STARTER_QUESTIONS
from copilot.trace import ToolCallRecord
from rag.config import RagSettings
from rag.retrieval import Retriever

ACCEPTANCE = STARTER_QUESTIONS[0]  # the brief's acceptance question, also the first question the GUI offers
OFF_TOPIC = ("conversation", "knowledge_question")  # a polite reply or "no relevant documents": both use no data


@dataclass(frozen=True)
class Case:
    question: str
    intent: str | tuple[str, ...]  # the acceptable intent(s)
    asset_id: str | None = None  # the asset the answer is about; None = not checked
    uses_data: bool = True  # at least one MCP tool call
    sources: tuple[str, ...] | None = None  # a source label must start with one of these; () = no sources at all
    gap: str | None = None  # text one of the answer's gaps must contain (an honest "not found")
    facts: tuple[str, ...] = ()  # text each of which must appear in the findings
    first_step: str | None = None  # a source label the first recommended action must cite
    procedure: bool | None = None  # the procedure section must be present (True) or absent (False)
    answer_type: str | tuple[str, ...] | None = None  # the agent's acceptable answer type(s), checked when it has one
    answer: tuple[str, ...] = ()  # regular expressions (ignoring case) that the direct answer must match
    model_only: bool = False  # needs the model: skipped offline; its answer must be model-written and typed
    history: tuple[str, ...] = ()  # earlier questions in the same chat, for "this alarm"


GOLDEN: list[Case] = [
    # why does an asset keep alarming
    Case(ACCEPTANCE, "investigate_asset", "AST-BFP-101", sources=("SOP-BFP-001 §4.1",),
         facts=("24", "PMP-VIB-HI"), first_step="SOP-BFP-001 §4.1", procedure=True),
    Case("Why does Boiler Feed Pump 101 keep vibrating?", "investigate_asset", "AST-BFP-101",
         sources=("SOP-BFP-001",), first_step="SOP-BFP-001 §4.1"),
    Case("Boiler Feed Pump 101 is making noise, what should I do?", "investigate_asset", "AST-BFP-101",
         sources=("SOP-BFP-001",)),
    Case("Why are compressor discharge pressure alarms repeatedly occurring?", "investigate_asset", "AST-CMP-301",
         sources=("SOP-CMP-004",), facts=("CMP-DISCH-PRESS-HI",), first_step="SOP-CMP-004"),
    Case("What is wrong with Gas Compressor C-301?", "investigate_asset", "AST-CMP-301", sources=("SOP-CMP-004",)),
    Case("Investigate Motor M-501 alarms over the last 30 days", "investigate_asset", "AST-MTR-501",
         sources=("TG-MTR-003",)),
    Case("Boiler B-201 how many times it failed in past?", "investigate_asset", "AST-BLR-201", sources=(),
         gap="no procedure for boiler alarms", facts=("24",), procedure=False),  # no boiler documents
    Case("Investigate Forced Draft Fan F-201", "investigate_asset", "AST-FAN-201", sources=("SAF-005",),
         gap="no procedure for fan alarms"),  # the safety instruction covers fans; no procedure does
    Case("Investigate Boiler Feed Pump 999", "investigate_asset", gap="No asset matches"),
    # what is active now
    Case("Show active critical alarms for Boiler Feed Pump 102 and recommend immediate actions.", "active_alarms",
         "AST-BFP-102", sources=("SOP-BFP-001",)),
    Case("Are there active alarms on Gas Compressor C-301 right now?", "active_alarms", "AST-CMP-301"),
    Case("What alarms are active right now in NorthPlant Unit 1?", "active_alarms"),
    Case("any boiler alarma are active", "active_alarms", gap="no active alarms on boiler assets"),  # a real chat
    Case("Are any pump alarms active?", "active_alarms", sources=("SOP-BFP-001",)),
    Case("check for all boilers", ("investigate_asset", "active_alarms"), sources=(), procedure=False),  # a real chat
    # what matters most
    Case("Which alarm has the highest priority in EastRefinery, and why?", "highest_priority", "AST-CMP-301",
         sources=("ALM-PHIL-006",)),
    Case("What is the most urgent alarm in NorthPlant right now?", "highest_priority", sources=("ALM-PHIL-006",)),
    # what else to inspect
    Case("What related assets should be inspected for this motor trip alarm?", "related_assets", "AST-MTR-501",
         sources=("TG-MTR-003 §6",)),
    Case("Which related equipment should I check for Boiler Feed Pump 101?", "related_assets", "AST-BFP-101"),
    # which procedure applies
    Case("Which procedure applies to Gas Compressor C-301 discharge pressure alarms?", "procedure_lookup",
         "AST-CMP-301", sources=("SOP-CMP-004",), first_step="SOP-CMP-004 §3.1", procedure=True),
    # is the Alarm API's advice in line with the documents
    Case("Do the Alarm API recommendations for Boiler Feed Pump 102 agree with the procedure?",
         "recommendation_check", "AST-BFP-102", sources=("SOP-BFP-001",)),
    # follow-ups that point back at the previous answer
    Case("Which operating procedure applies to this alarm?", "procedure_lookup", "AST-BFP-101",
         sources=("SOP-BFP-001 §4.1",), procedure=True, history=(ACCEPTANCE,)),
    Case("Are the API recommendations consistent with the maintenance manual?", "recommendation_check",
         "AST-BFP-101", sources=("SOP-BFP-001 §4.1",), history=(ACCEPTANCE,)),
    Case("What related assets should be inspected for this alarm?", "related_assets", "AST-BFP-101",
         history=(ACCEPTANCE,)),
    Case("Are there active alarms on Boiler Feed Pump 101 right now?", "active_alarms", "AST-BFP-101",
         history=(ACCEPTANCE,)),
    # documents only
    Case("What does the safety instruction say about lockout?", "knowledge_question", uses_data=False,
         sources=("SAF-005",)),
    Case("How quickly must a P1 alarm be responded to?", "knowledge_question", uses_data=False,
         sources=("ALM-PHIL-006",)),
    Case("What should the operator do during an alarm flood?", "knowledge_question", uses_data=False,
         sources=("ALM-PHIL-006",)),
    Case("When an AI recommendation disagrees with the operating procedure, which one wins?", "knowledge_question",
         uses_data=False, sources=("ALM-PHIL-006 §7",)),
    # small talk and off-topic: no data, no documents
    Case("hello", "conversation", uses_data=False, sources=(), procedure=False),
    Case("thanks!", "conversation", uses_data=False, sources=()),
    Case("What can you do?", "conversation", uses_data=False, sources=()),
    Case("Tell me a joke", OFF_TOPIC, uses_data=False, sources=()),
    Case("What is the capital of France?", OFF_TOPIC, uses_data=False, sources=()),
    # no playbook covers these: the agent combines tools (skipped offline)
    Case("How many refineries do we have?", ("agent", "active_alarms"), answer_type="count",
         answer=(r"\b(1|one)\b", r"East ?Refinery"), model_only=True),
    Case("Compare Boiler Feed Pump 101 and Boiler Feed Pump 102 over the last 90 days", ("agent", "investigate_asset"),
         answer_type="compare", model_only=True,
         answer=(r"101\D{0,60}?\b42\b|\b42\b\D{0,60}?101", r"102\D{0,60}?\b20\b|\b20\b\D{0,60}?102")),
    Case("Which motor alarmed most in the last 90 days?", ("agent", "investigate_asset"),
         answer_type=("list", "compare", "count"), answer=(r"Motor M-101",), model_only=True),
    Case("How many assets are at EastRefinery?", "agent", answer_type="count", answer=(r"\b(7|seven)\b",),
         model_only=True),
]  # fmt: skip


@dataclass
class EvalRow:
    case: Case
    problems: list[str]  # empty = passed
    response: ChatResponse  # the last answer, for the run's counts


@dataclass
class EvalReport:
    rows: list[EvalRow]

    @property
    def passed(self) -> int:
        return sum(not r.problems for r in self.rows)

    @property
    def failed(self) -> list[EvalRow]:
        return [r for r in self.rows if r.problems]

    def counts(self) -> dict[str, int]:
        """What the model did across the run (zero without a model)."""
        answers = [r.response.answer for r in self.rows]
        understood = [r.response.understanding for r in self.rows]
        return {
            "summaries rejected by the check": sum(a.grounding.summary_rejected is not None for a in answers),
            "summaries whose keys code added": sum(a.grounding.summary_keys_added for a in answers),
            "summaries shortened to fit": sum(a.grounding.summary_shortened for a in answers),
            "statements removed by the checks": sum(a.grounding.removed_statements for a in answers),
            "answers with an analysis": sum(bool(a.analysis) for a in answers),
            "model and rules disagreed on the intent": sum(
                u.rules_intent is not None and u.rules_intent != u.intent for u in understood
            ),
            "agent steps": sum(a.grounding.agent_steps for a in answers),
            "stopped at the step limit": sum(a.grounding.agent_hit_limit for a in answers),
        }


def check(case: Case, response: ChatResponse) -> list[str]:
    """What is wrong with this answer, one line per check that failed."""
    problems = []
    intents = (case.intent,) if isinstance(case.intent, str) else case.intent
    if response.understanding.intent not in intents:
        problems.append(f"intent: {response.understanding.intent}, expected {' or '.join(intents)}")
    asset = (response.evidence.asset or {}).get("asset_id")
    if case.asset_id is not None and asset != case.asset_id:
        problems.append(f"asset: {asset}, expected {case.asset_id}")
    used = any(isinstance(step, ToolCallRecord) for step in response.trace)
    if used != case.uses_data:
        problems.append("live data: used, expected none" if used else "live data: none, expected some")
    labels = [c.label for c in response.answer.citations]
    if case.sources == () and labels:
        problems.append(f"sources: {', '.join(labels)}, expected none")
    elif case.sources and not any(label.startswith(case.sources) for label in labels):
        problems.append(f"sources: {', '.join(labels) or 'none'}, expected {' or '.join(case.sources)}")
    if case.gap and not any(case.gap.lower() in g.lower() for g in response.answer.gaps):
        problems.append(f"gap: none mentions '{case.gap}'")
    findings = " ".join(s.text for s in response.answer.findings)
    if missing := [f for f in case.facts if f not in findings]:
        problems.append(f"facts: {', '.join(missing)} not in the findings")
    if case.first_step:
        first = response.answer.recommended_actions[:1]
        cited = [c.label for c in response.answer.citations if first and c.key in first[0].citations]
        if not any(label.startswith(case.first_step) for label in cited):
            problems.append(f"first step: cites {', '.join(cited) or 'nothing'}, expected {case.first_step}")
    if case.procedure is not None and bool(response.answer.procedure) != case.procedure:
        problems.append("procedure: missing" if case.procedure else "procedure: present, expected none")
    if case.model_only and (response.answer.grounding.summary_source != "llm" or response.answer.answer_type is None):
        problems.append("agent: no model-written direct answer")
    if case.answer_type and response.answer.answer_type:
        types = (case.answer_type,) if isinstance(case.answer_type, str) else case.answer_type
        if response.answer.answer_type not in types:
            problems.append(f"answer type: {response.answer.answer_type}, expected {' or '.join(types)}")
    if missing := [p for p in case.answer if not re.search(p, response.answer.summary, re.IGNORECASE)]:
        problems.append(f"answer: {', '.join(missing)} not in the direct answer")
    if response.answer.grounding.unknown_citations:
        problems.append(f"citations: rejected {', '.join(response.answer.grounding.unknown_citations)}")
    return problems


def notes(response: ChatResponse) -> str:
    """How the question was understood, and why the model's summary was not shown (when it was not)."""
    u = response.understanding
    seen = ", ".join(
        f"{k} {v}" for k, v in (("asset", u.asset_query), ("type", u.asset_type), ("topic", u.alarm_topic)) if v
    )
    out = [f"understood as {u.intent}" + (f" ({seen})" if seen else "")]
    g = response.answer.grounding
    if g.summary_keys_added:
        out.append("keys added by code")
    if g.summary_shortened:
        out.append("summary shortened to fit")
    if g.summary_rejected:
        out.append(f"summary rejected: {g.summary_rejected}")
    if g.rejected_summary:
        wrote = " ".join(g.rejected_summary.replace("|", "/").split())  # one table cell: no pipes, no line breaks
        out.append(f'model wrote: "{wrote}"')
    return "; ".join(out)


async def services_down(service: CopilotService) -> str | None:
    """Why the set cannot run against these services, or None. Checked first, so no model request is spent on a
    run whose alarm data would all be missing."""
    tools = await service.list_tools()
    if tools.connected:
        return None
    return f"The MCP server is unreachable ({tools.error}). Start the Alarm API and the MCP server first."


def facts_part(response: ChatResponse) -> list[tuple[str, tuple[str, ...]]]:
    """The answer's code-built sections, to compare two answers to the same question."""
    a = response.answer
    return [(s.text, tuple(s.citations)) for s in (*a.findings, *a.procedure, *a.recommended_actions)]


async def _ask(service: CopilotService, case: Case) -> ChatResponse:
    conversation_id = None
    for question in (*case.history, case.question):
        response = await service.ask(question, conversation_id)
        conversation_id = response.conversation_id
    return response


async def evaluate(service: CopilotService, cases: Sequence[Case] = GOLDEN, repeat: bool = False) -> EvalReport:
    rows = []
    for case in cases:
        if case.model_only and service.llm is None:
            continue
        response = await _ask(service, case)
        problems = check(case, response)
        if repeat and facts_part(await _ask(service, case)) != facts_part(response):
            problems.append("repeat: the facts part changed when asked again")
        rows.append(EvalRow(case, problems, response))
    return EvalReport(rows)


def write_report(report: EvalReport, path: Path, mode: str) -> None:
    """The run as Markdown: the score, the counts, and every question with its problems."""
    lines = [
        f"# Answer-quality run, {date.today().isoformat()}",
        "",
        f"{report.passed}/{len(report.rows)} passed ({mode}).",
        "",
        "| Count | |",
        "|---|---|",
        *(f"| {name} | {value} |" for name, value in report.counts().items()),
        "",
        "| Question | Result | Problems | Notes |",
        "|---|---|---|---|",
        *(
            f"| {r.case.question} | {'FAIL' if r.problems else 'pass'} | {'; '.join(r.problems)} | "
            f"{notes(r.response)} |"
            for r in report.rows
        ),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the copilot's golden question set.")
    parser.add_argument("--llm", action="store_true", help="use the configured LLM (costs requests)")
    parser.add_argument("--skip", type=int, default=0, help="skip the first N questions (to run the set in parts)")
    parser.add_argument("--limit", type=int, help="run only N questions")
    parser.add_argument("--repeat", action="store_true", help="ask every question twice and compare the facts")
    parser.add_argument("--report", type=Path, help="write the results as Markdown to this file")
    args = parser.parse_args(argv)

    settings = CopilotSettings()
    llm = None
    if args.llm:
        key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
        llm = build_provider(settings.llm_provider, settings.llm_model, key, settings.llm_thinking_level)
        if llm is None:
            print("No LLM is configured (LLM_PROVIDER and LLM_API_KEY in .env).")
            return 2
    token = settings.mcp_auth_token.get_secret_value() if settings.mcp_auth_token else ""
    service = CopilotService(
        http_connector(settings.mcp_server_url, token),
        Retriever.from_settings(RagSettings()),
        llm,
        conversations=ConversationStore(),  # in memory: evaluation chats are never saved
    )
    if problem := asyncio.run(services_down(service)):
        print(problem)
        return 2
    report = asyncio.run(evaluate(service, GOLDEN[args.skip :][: args.limit], repeat=args.repeat))
    for row in report.rows:
        print(f"{'FAIL' if row.problems else 'pass'}  {row.case.question[:90]}")
        for problem in row.problems:
            print(f"      - {problem}")
        if llm:
            print(f"      ({notes(row.response)})")
    mode = "LLM" if llm else "keyword rules and template answers"
    print(f"\n{report.passed}/{len(report.rows)} passed ({mode})")
    if llm:
        for name, value in report.counts().items():
            print(f"  {name}: {value}")
    if args.report:
        write_report(report, args.report, mode)
        print(f"Report written to {args.report}")
    return 0 if not report.failed else 1


if __name__ == "__main__":
    sys.exit(main())
