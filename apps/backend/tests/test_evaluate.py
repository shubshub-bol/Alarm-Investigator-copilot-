"""The copilot's golden question set, run offline (keyword rules, template answers) on an in-memory stack."""

import pytest

from copilot.evaluate import GOLDEN, Case, evaluate, facts_part, services_down, write_report
from copilot.mcp_client import http_connector, in_memory_connector
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
    grounding = report.rows[0].response.answer.grounding
    grounding.summary_rejected = "number 31 is not in the evidence"
    grounding.rejected_summary = "Boiler Feed Pump 101 had 31 alarms | rising"
    write_report(report, tmp_path / "run.md", "LLM")
    text = (tmp_path / "run.md").read_text(encoding="utf-8")
    assert "understood as investigate_asset (asset Boiler Feed Pump 101, type pump)" in text
    assert "summary rejected: number 31 is not in the evidence" in text
    assert 'model wrote: "Boiler Feed Pump 101 had 31 alarms / rising"' in text  # a | would break the table


async def test_the_report_has_unix_line_endings_like_the_rest_of_the_repository(service, tmp_path):
    write_report(await evaluate(service, GOLDEN[:1]), tmp_path / "run.md", "offline")
    assert b"\r\n" not in (tmp_path / "run.md").read_bytes()


async def test_the_evaluation_does_not_start_when_the_mcp_server_is_unreachable():
    dead = CopilotService(http_connector("http://127.0.0.1:9/mcp", token="x", timeout_seconds=2), None, llm=None)
    assert "MCP server is unreachable" in await services_down(dead)  # no model requests spent on a broken stack


async def test_the_evaluation_starts_when_the_services_are_up(service):
    assert await services_down(service) is None


async def test_the_report_counts_summaries_whose_keys_code_added(service, tmp_path):
    report = await evaluate(service, GOLDEN[:1])
    report.rows[0].response.answer.grounding.summary_keys_added = True
    write_report(report, tmp_path / "run.md", "LLM")
    text = (tmp_path / "run.md").read_text(encoding="utf-8")
    assert "| summaries whose keys code added | 1 |" in text
    assert "keys added by code" in text


async def test_the_report_counts_shortened_summaries(service, tmp_path):
    report = await evaluate(service, GOLDEN[:1])
    report.rows[0].response.answer.grounding.summary_shortened = True
    write_report(report, tmp_path / "run.md", "LLM")
    text = (tmp_path / "run.md").read_text(encoding="utf-8")
    assert "| summaries shortened to fit | 1 |" in text
    assert "summary shortened" in text
