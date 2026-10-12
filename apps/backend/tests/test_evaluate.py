"""The copilot's golden question set, run offline (keyword rules, template answers) on an in-memory stack."""

import pytest

from copilot.evaluate import GOLDEN, Case, evaluate, facts_part, write_report
from copilot.mcp_client import in_memory_connector
from copilot.service import CopilotService
from copilot_test_helpers import simulator_transport
from mcp_test_helpers import make_server


@pytest.fixture
def service(retriever):
    return CopilotService(in_memory_connector(make_server(simulator_transport())), retriever, llm=None)


async def test_every_golden_question_passes_offline(service):
    report = await evaluate(service)
    assert {row.case.question: row.problems for row in report.failed} == {}
    assert report.passed == len(GOLDEN)


async def test_a_wrong_answer_is_reported_with_every_problem(service):
    wrong = Case(
        "Which alarm has the highest priority in EastRefinery, and why?",
        intent="active_alarms",  # it is highest_priority
        asset_id="AST-BFP-101",  # it is AST-CMP-301
        uses_data=False,  # it calls MCP tools
        sources=("SAF-005",),  # this playbook does not search the safety instruction
        gap="no such gap",
        facts=("999 alarms",),
        first_step="SAF-005",  # the first step is the compressor procedure's
        procedure=False,  # the answer has a procedure section
    )
    report = await evaluate(service, [wrong])
    assert report.passed == 0
    assert [p.split(":")[0] for p in report.rows[0].problems] == [
        "intent", "asset", "live data", "sources", "gap", "facts", "first step", "procedure"
    ]  # fmt: skip


async def test_asking_twice_offline_gives_the_same_facts(service):
    report = await evaluate(service, GOLDEN[:2], repeat=True)
    assert report.failed == []


async def test_facts_part_tells_two_answers_apart(service):
    response = (await evaluate(service, GOLDEN[:1])).rows[0].response
    changed = response.model_copy(deep=True)
    changed.answer.findings[0].text += " (changed)"
    assert facts_part(changed) != facts_part(response)


async def test_the_report_has_the_score_and_every_question(service, tmp_path):
    report = await evaluate(service, GOLDEN[:2])
    write_report(report, tmp_path / "run.md", "offline")
    text = (tmp_path / "run.md").read_text(encoding="utf-8")
    assert "2/2 passed (offline)." in text
    for case in GOLDEN[:2]:
        assert case.question in text


async def test_the_report_says_how_each_question_was_understood_and_why_a_summary_was_rejected(service, tmp_path):
    report = await evaluate(service, GOLDEN[:1])
    report.rows[0].response.answer.grounding.summary_rejected = "number 31 is not in the evidence"
    write_report(report, tmp_path / "run.md", "LLM")
    text = (tmp_path / "run.md").read_text(encoding="utf-8")
    assert "understood as investigate_asset (asset Boiler Feed Pump 101, type pump)" in text
    assert "summary rejected: number 31 is not in the evidence" in text
