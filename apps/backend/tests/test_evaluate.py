"""The copilot's golden question set, run offline (keyword rules, template answers) on an in-memory stack."""

import pytest

from copilot.evaluate import GOLDEN, Case, evaluate
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
    )
    report = await evaluate(service, [wrong])
    assert report.passed == 0
    assert [p.split(":")[0] for p in report.rows[0].problems] == ["intent", "asset", "live data", "sources", "gap"]
