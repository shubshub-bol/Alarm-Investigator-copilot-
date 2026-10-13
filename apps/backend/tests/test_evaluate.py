"""The copilot's golden question set, run offline (keyword rules, template answers) on an in-memory stack."""

import pytest

from copilot.evaluate import GOLDEN, Case, check, evaluate, facts_part, notes, services_down, write_report
from copilot.mcp_client import http_connector, in_memory_connector
from copilot.service import ChatResponse, CopilotService
from copilot_test_helpers import simulator_transport
from mcp_test_helpers import make_server


@pytest.fixture
def service(retriever):
    return CopilotService(in_memory_connector(make_server(simulator_transport())), retriever, llm=None)


async def test_every_golden_question_passes_offline(service):
    report = await evaluate(service)
    assert {row.case.question: row.problems for row in report.failed} == {}
    assert report.passed == len([c for c in GOLDEN if not c.model_only])


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


async def test_the_report_shows_each_answer_and_the_tools_behind_it(service, tmp_path):
    """A real-model run is judged by what the model said, not only by the checks' verdicts."""
    report = await evaluate(service, GOLDEN[:1])
    write_report(report, tmp_path / "run.md", "offline")
    text = (tmp_path / "run.md").read_text(encoding="utf-8")
    assert "## Answers" in text
    assert f"> {report.rows[0].response.answer.summary}" in text
    assert "Tools: search_assets, get_asset_metadata, summarize_alarms" in text


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


async def test_model_only_questions_are_skipped_without_a_model(service):
    report = await evaluate(service, [Case("How many refineries do we have?", "agent", model_only=True)])
    assert report.rows == []


async def test_the_answer_type_and_the_direct_answer_are_checked(service):
    response = (await evaluate(service, GOLDEN[:1])).rows[0].response
    response.answer.answer_type = "compare"
    case = Case(GOLDEN[0].question, "investigate_asset", answer_type="count", answer=("999 alarms",))
    assert [p.split(":")[0] for p in check(case, response)] == ["answer type", "answer"]


REFINERIES = "How many refineries do we have?"
COMPARE = "Compare Boiler Feed Pump 101 and Boiler Feed Pump 102 over the last 90 days"
MOST_ALARMS = "Which motor alarmed most in the last 90 days?"
ASSETS = "How many assets are at EastRefinery?"


def golden(question: str) -> Case:
    return next(case for case in GOLDEN if case.question == question)


def answer_problems(case: Case, response: ChatResponse) -> list[str]:
    return [p for p in check(case, response) if p.startswith("answer:")]


async def test_a_model_only_answer_must_be_written_by_the_model(service):
    response = (await evaluate(service, GOLDEN[:1])).rows[0].response
    case = golden(MOST_ALARMS)
    response.answer.grounding.summary_source = "template"
    response.answer.answer_type = None
    assert "agent: no model-written direct answer" in check(case, response)
    response.answer.grounding.summary_source = "llm"
    assert "agent: no model-written direct answer" in check(case, response)  # the answer type is still missing
    response.answer.answer_type = "list"
    assert "agent: no model-written direct answer" not in check(case, response)


async def test_the_refinery_count_needs_the_whole_number_one_and_the_refinery(service):
    response = (await evaluate(service, GOLDEN[:1])).rows[0].response
    case = golden(REFINERIES)
    response.answer.summary = "There are 11 refineries, including EastRefinery [T1]."
    assert answer_problems(case, response) == [f"answer: {case.answer[0]} not in the direct answer"]
    response.answer.summary = "There is one refinery: East Refinery [T1]."
    assert answer_problems(case, response) == []


async def test_the_compare_answer_must_tie_each_count_to_its_pump(service):
    response = (await evaluate(service, GOLDEN[:1])).rows[0].response
    case = golden(COMPARE)
    response.answer.grounding.summary_source = "llm"
    response.answer.answer_type = "compare"
    response.answer.summary = (
        "Boiler Feed Pump 101 had 42 alarms and Boiler Feed Pump 102 had 18 since 2026-04-02 [T1]."
    )
    assert answer_problems(case, response) == [f"answer: {case.answer[1]} not in the direct answer"]
    response.answer.summary = "Boiler Feed Pump 101 had 42 alarms and Boiler Feed Pump 102 had 20 [T1]."
    assert check(case, response) == []


async def test_a_count_must_be_a_whole_number(service):
    response = (await evaluate(service, GOLDEN[:1])).rows[0].response
    case = golden(ASSETS)
    response.answer.summary = "17 assets [T7]."
    assert answer_problems(case, response) == [f"answer: {case.answer[0]} not in the direct answer"]
    response.answer.summary = "7 assets [T1]."
    assert answer_problems(case, response) == []


async def test_an_answer_type_can_be_one_of_several(service):
    response = (await evaluate(service, GOLDEN[:1])).rows[0].response
    case = Case(GOLDEN[0].question, "investigate_asset", answer_type=("list", "compare"))
    response.answer.answer_type = "compare"
    assert check(case, response) == []
    response.answer.answer_type = "why"
    assert check(case, response) == ["answer type: why, expected list or compare"]


async def test_the_direct_answer_patterns_ignore_case(service):
    response = (await evaluate(service, GOLDEN[:1])).rows[0].response
    response.answer.summary = "motor m-101 had 24 alarms [T1]."
    assert answer_problems(golden(MOST_ALARMS), response) == []


def intent_problems(case: Case, response: ChatResponse) -> list[str]:
    return [p for p in check(case, response) if p.startswith("intent:")]


def answered_by_the_agent(response: ChatResponse, intent: str) -> ChatResponse:
    """The same response as if the agent had answered by another route."""
    u = response.understanding.model_copy(update={"intent": intent, "method": "llm"})
    return response.model_copy(update={"understanding": u})


async def test_with_a_model_the_route_is_not_checked(service):
    response = (await evaluate(service, GOLDEN[:1])).rows[0].response
    assert intent_problems(GOLDEN[0], answered_by_the_agent(response, "active_alarms")) == []


async def test_on_the_rules_path_the_route_is_checked(service):
    response = (await evaluate(service, GOLDEN[:1])).rows[0].response
    wrong = response.model_copy(
        update={"understanding": response.understanding.model_copy(update={"intent": "active_alarms"})}
    )
    assert intent_problems(GOLDEN[0], wrong) != []


async def test_the_report_lists_each_answers_document_searches(service, tmp_path):
    report = await evaluate(service, GOLDEN[:1])
    write_report(report, tmp_path / "run.md", "offline")
    text = (tmp_path / "run.md").read_text(encoding="utf-8")
    assert "Searches: pump High Vibration (PMP-VIB-HI)" in text


B201 = "Boiler B-201 how many times it failed in past?"


def fact_problems(case: Case, response: ChatResponse) -> list[str]:
    return [p for p in check(case, response) if p.startswith("facts:")]


def shown(response: ChatResponse, finding: str, summary: str) -> ChatResponse:
    """The same response with one finding and a direct answer: what the operator reads."""
    response = response.model_copy(deep=True)
    response.answer.findings = response.answer.findings[:1]
    response.answer.findings[0].text = finding
    response.answer.summary = summary
    return response


@pytest.mark.parametrize("finding", ["Alarm ALM-000424 tripped", "Trip logged at 12:24:00", "Since 2024", "Load 0.24"])
async def test_a_24_inside_an_id_a_year_or_a_time_is_not_the_count(service, finding):
    response = (await evaluate(service, [golden(B201)])).rows[0].response
    changed = shown(response, finding, "No count was found [T1].")
    assert fact_problems(golden(B201), changed) == ["facts: 24 not in the findings or the direct answer"]


@pytest.mark.parametrize("summary", ["Boiler B-201 had 24 alarms [T1].", "Boiler B-201 had 24."])
async def test_a_24_in_the_direct_answer_counts_as_shown(service, summary):
    response = (await evaluate(service, [golden(B201)])).rows[0].response
    changed = shown(response, "Failure count since 2026-04-02", summary)
    assert fact_problems(golden(B201), changed) == []


async def test_the_count_is_found_in_the_offline_findings(service):
    response = (await evaluate(service, [golden(B201)])).rows[0].response
    assert fact_problems(golden(B201), response) == []


async def test_a_data_case_answered_without_data_is_reported(service):
    documents = (await evaluate(service, [golden("What does the safety instruction say about lockout?")])).rows[0]
    assert "live data: none, expected some" in check(GOLDEN[0], documents.response)


async def test_the_notes_give_the_agents_steps_and_why_it_stopped(service):
    response = (await evaluate(service, GOLDEN[:1])).rows[0].response
    g = response.answer.grounding
    g.agent_steps, g.agent_hit_limit, g.degraded, g.degraded_reason = 6, True, True, "timeout"
    text = notes(response)
    assert "6 agent steps" in text and "stopped at the step limit" in text and "model error: timeout" in text


async def test_the_refinery_count_may_say_only_or_single(service):
    response = (await evaluate(service, GOLDEN[:1])).rows[0].response
    for summary in ("EastRefinery is the only refinery [T1].", "There is a single refinery, East Refinery [T1]."):
        response.answer.summary = summary
        assert answer_problems(golden(REFINERIES), response) == []
