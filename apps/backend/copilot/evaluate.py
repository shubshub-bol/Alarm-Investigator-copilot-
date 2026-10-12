"""Copilot evaluation on a golden question set: each question must get the right kind of investigation, the right
asset, live data when it needs it, the right documents, and only valid citations.

    python -m copilot.evaluate                    # keyword rules and template answers: no LLM requests
    python -m copilot.evaluate --llm --limit 5    # the configured LLM: about 3 requests a question

It needs the Alarm API and the MCP server running, as the backend does. Its chats stay in memory, never in the
saved chat history. The test suite runs the same set offline on an in-memory stack (tests/test_evaluate.py).
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass

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
    history: tuple[str, ...] = ()  # earlier questions in the same chat, for "this alarm"


GOLDEN: list[Case] = [
    # why does an asset keep alarming
    Case(ACCEPTANCE, "investigate_asset", "AST-BFP-101", sources=("SOP-BFP-001 §4.1",)),
    Case("Why does Boiler Feed Pump 101 keep vibrating?", "investigate_asset", "AST-BFP-101", sources=("SOP-BFP-001",)),
    Case("Boiler Feed Pump 101 is making noise, what should I do?", "investigate_asset", "AST-BFP-101",
         sources=("SOP-BFP-001",)),
    Case("Why are compressor discharge pressure alarms repeatedly occurring?", "investigate_asset", "AST-CMP-301",
         sources=("SOP-CMP-004",)),
    Case("What is wrong with Gas Compressor C-301?", "investigate_asset", "AST-CMP-301", sources=("SOP-CMP-004",)),
    Case("Investigate Motor M-501 alarms over the last 30 days", "investigate_asset", "AST-MTR-501",
         sources=("TG-MTR-003",)),
    Case("Boiler B-201 how many times it failed in past?", "investigate_asset", "AST-BLR-201"),  # no boiler documents
    Case("Investigate Boiler Feed Pump 999", "investigate_asset", gap="No asset matches"),
    # what is active now
    Case("Show active critical alarms for Boiler Feed Pump 102 and recommend immediate actions.", "active_alarms",
         "AST-BFP-102", sources=("SOP-BFP-001",)),
    Case("Are there active alarms on Gas Compressor C-301 right now?", "active_alarms", "AST-CMP-301"),
    Case("What alarms are active right now in NorthPlant Unit 1?", "active_alarms"),
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
         "AST-CMP-301", sources=("SOP-CMP-004",)),
    # is the Alarm API's advice in line with the documents
    Case("Do the Alarm API recommendations for Boiler Feed Pump 102 agree with the procedure?",
         "recommendation_check", "AST-BFP-102", sources=("SOP-BFP-001",)),
    # follow-ups that point back at the previous answer
    Case("Which operating procedure applies to this alarm?", "procedure_lookup", "AST-BFP-101",
         sources=("SOP-BFP-001 §4.1",), history=(ACCEPTANCE,)),
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
    Case("hello", "conversation", uses_data=False, sources=()),
    Case("thanks!", "conversation", uses_data=False, sources=()),
    Case("What can you do?", "conversation", uses_data=False, sources=()),
    Case("Tell me a joke", OFF_TOPIC, uses_data=False, sources=()),
    Case("What is the capital of France?", OFF_TOPIC, uses_data=False, sources=()),
]  # fmt: skip


@dataclass
class EvalRow:
    case: Case
    problems: list[str]  # empty = passed


@dataclass
class EvalReport:
    rows: list[EvalRow]

    @property
    def passed(self) -> int:
        return sum(not r.problems for r in self.rows)

    @property
    def failed(self) -> list[EvalRow]:
        return [r for r in self.rows if r.problems]


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
    if response.answer.grounding.unknown_citations:
        problems.append(f"citations: rejected {', '.join(response.answer.grounding.unknown_citations)}")
    return problems


async def evaluate(service: CopilotService, cases: Sequence[Case] = GOLDEN) -> EvalReport:
    rows = []
    for case in cases:
        conversation_id = None
        for question in (*case.history, case.question):
            response = await service.ask(question, conversation_id)
            conversation_id = response.conversation_id
        rows.append(EvalRow(case, check(case, response)))
    return EvalReport(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the copilot's golden question set.")
    parser.add_argument("--llm", action="store_true", help="use the configured LLM (costs requests)")
    parser.add_argument("--limit", type=int, help="run only the first N questions")
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
    report = asyncio.run(evaluate(service, GOLDEN[: args.limit]))
    for row in report.rows:
        print(f"{'FAIL' if row.problems else 'pass'}  {row.case.question[:90]}")
        for problem in row.problems:
            print(f"      - {problem}")
    mode = "LLM" if llm else "keyword rules and template answers"
    print(f"\n{report.passed}/{len(report.rows)} passed ({mode})")
    return 0 if not report.failed else 1


if __name__ == "__main__":
    sys.exit(main())
