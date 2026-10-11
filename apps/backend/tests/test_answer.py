"""Evidence comparison and grounded answers: tool facts [T#], document citations [S#], conflicts, degradation."""

import httpx
import pytest

from copilot.answer import LlmAnswer, compose_answer
from copilot.consistency import assess_actions
from copilot.investigation import ConversationState, investigate
from copilot.llm import LlmError
from copilot.mcp_client import McpToolClient, in_memory_connector
from copilot.understanding import understand_with_rules
from copilot_test_helpers import simulator_transport
from mcp_test_helpers import make_server

ACCEPTANCE = (
    "Investigate recurring high-severity alarms for Boiler Feed Pump 101 over the last 90 days, identify likely "
    "contributing factors, retrieve the relevant operating procedure, and provide recommended actions."
)


@pytest.fixture
async def acceptance_run(mcp, retriever):
    return await investigate(understand_with_rules(ACCEPTANCE), mcp, retriever, ConversationState(), trace_id="t")


def all_statements(answer):
    return answer.findings + answer.likely_causes + answer.procedure + answer.recommended_actions


def key_for(answer, label):
    return next(c.key for c in answer.citations if c.label == label)


# --------------------------------------------------------------- conflict check


async def test_api_action_that_contradicts_the_procedure_is_a_conflict(acceptance_run):
    ev = acceptance_run.evidence
    verdicts = {a.action: a for a in assess_actions(ev.recommendations["recommended_actions"], ev.passages)}
    keep_running = verdicts["Continue operation and monitor vibration trend for 24 hours"]
    assert keep_running.verdict == "conflict"
    sop = next(p for p in ev.passages if p.citation.label == "SOP-BFP-001 §4.1")
    assert sop.citation.key in keep_running.citation_keys
    assert "7.1 mm/s" in keep_running.document_guidance or "standby" in keep_running.document_guidance
    assert verdicts["Check drive motor bearing temperature"].verdict == "consistent"


async def test_conflict_pulls_in_the_governance_rule(acceptance_run):
    assert "ALM-PHIL-006 §7" in {p.citation.label for p in acceptance_run.evidence.passages}


# ------------------------------------------------------------- template answer


async def test_template_answer_is_grounded_and_structured(acceptance_run):
    answer = await compose_answer(ACCEPTANCE, acceptance_run, llm=None)
    assert answer.grounding.mode == "template"
    assert answer.summary
    for statement in all_statements(answer):
        assert statement.citations, f"uncited: {statement.text}"
    assert {f.tool for f in answer.facts} >= {"summarize_alarms", "correlate_alarms", "get_operator_recommendations"}
    assert any("Motor M-101" in s.text for s in answer.likely_causes)
    assert any(s.text.startswith("Related asset Motor M-101") for s in answer.findings)  # related-asset status shown
    assert any(s.text.startswith("History:") for s in answer.findings)
    assert key_for(answer, "SOP-BFP-001 §4.1") in {k for s in answer.procedure for k in s.citations}


async def test_template_actions_put_the_procedure_first_and_include_safety(acceptance_run):
    answer = await compose_answer(ACCEPTANCE, acceptance_run, llm=None)
    first = answer.recommended_actions[0]
    assert key_for(answer, "SOP-BFP-001 §4.1") in first.citations
    assert "Continue operation" not in first.text or "instead of" in first.text
    safety_key = key_for(answer, "SAF-005 §2")
    assert any(safety_key in s.citations for s in answer.recommended_actions)
    conflict = next(a for a in answer.action_assessments if a.verdict == "conflict")
    assert key_for(answer, "ALM-PHIL-006 §7") in conflict.citation_keys


async def test_markdown_shows_both_citation_kinds_and_the_conflict(acceptance_run):
    md = (await compose_answer(ACCEPTANCE, acceptance_run, llm=None)).markdown
    assert "[T" in md and "[S" in md
    assert "conflict" in md.lower()
    assert "### Recommended actions" in md


async def test_facts_point_at_successful_trace_steps(acceptance_run):
    answer = await compose_answer(ACCEPTANCE, acceptance_run, llm=None)
    steps = {s.step: s for s in acceptance_run.steps}
    for fact in answer.facts:
        assert steps[fact.step].status == "ok"
        assert steps[fact.step].tool == fact.tool


async def test_asset_fact_lists_the_related_assets_that_were_looked_up(acceptance_run):
    answer = await compose_answer(ACCEPTANCE, acceptance_run, llm=None)
    asset_fact = next(f for f in answer.facts if f.tool == "get_asset_metadata")
    assert "Related assets: Motor M-101 (BFP 101 Drive)" in asset_fact.statement


async def test_follow_up_does_not_claim_related_assets_it_never_looked_up(mcp, retriever):
    """The follow-up reloads only the alarm (get_alarm_details), so the related assets are unknown, not 'none'."""
    state = ConversationState()
    await investigate(understand_with_rules(ACCEPTANCE), mcp, retriever, state, trace_id="t1")
    question = "Which operating procedure applies to this alarm?"
    run = await investigate(understand_with_rules(question), mcp, retriever, state, trace_id="t2")
    assert "get_asset_metadata" not in {s.tool for s in run.steps if s.kind == "mcp_tool"}  # never looked up

    answer = await compose_answer(question, run, llm=None)
    asset_fact = next((f for f in answer.facts if f.statement.startswith("Boiler Feed Pump 101 (AST-BFP-101)")), None)
    assert asset_fact is not None  # the asset is still described
    assert "none recorded" not in asset_fact.statement


# ------------------------------------------------------------------- LLM answers


class FakeLlm:
    name = "fake"

    def __init__(self, reply=None, error=None):
        self.reply, self.error = reply, error

    async def generate_json(self, system, prompt, schema):
        if self.error:
            raise self.error
        self.prompt = prompt
        return schema.model_validate(self.reply)


async def test_llm_answer_citations_are_validated(acceptance_run):
    reply = {
        "summary": "Vibration on BFP-101 is recurring and rising [T2].",
        "findings": [
            {"text": "24 high vibration alarms in 90 days.", "citations": ["T2"]},
            {"text": "Invented statistic.", "citations": ["T99"]},
        ],
        "likely_causes": [{"text": "Coupling misalignment.", "citations": ["S99"]}],
        "procedure": [{"text": "Change over to the standby pump above 7.1 mm/s.", "citations": ["S2", "S42"]}],
        "recommended_actions": [{"text": "Start the standby pump.", "citations": ["S2"]}],
        "action_assessments": [],
        "gaps": [],
    }
    llm = FakeLlm(reply=reply)
    answer = await compose_answer(ACCEPTANCE, acceptance_run, llm=llm)
    assert answer.grounding.mode == "llm"
    texts = [s.text for s in all_statements(answer)]
    assert "Invented statistic." not in texts and "Coupling misalignment." not in texts
    assert answer.procedure[0].citations == ["S2"]
    assert set(answer.grounding.unknown_citations) == {"T99", "S99", "S42"}
    assert answer.grounding.removed_statements == 2
    assert "UNTRUSTED" in llm.prompt.upper() or "untrusted" in llm.prompt  # documents passed as untrusted data


async def test_grouped_inline_citations_are_parsed_and_validated(acceptance_run):
    """Seen with Gemini: it writes '[T2, T3]' inside the text."""
    reply = {
        "summary": "Vibration is rising [T2, T99] and needs action [S2].",
        "findings": [{"text": "24 alarms, rising 43% [T2, T3].", "citations": []}],
        "recommended_actions": [{"text": "Change over to the standby pump [S2; S77].", "citations": ["S2"]}],
    }
    answer = await compose_answer(ACCEPTANCE, acceptance_run, llm=FakeLlm(reply=reply))
    assert answer.summary == "Vibration is rising [T2] and needs action [S2]."
    assert answer.findings[0].text == "24 alarms, rising 43%."
    assert answer.findings[0].citations == ["T2", "T3"]
    assert answer.recommended_actions[0].citations == ["S2"]
    assert {"T99", "S77"} <= set(answer.grounding.unknown_citations)


async def test_missing_llm_assessments_are_filled_and_conflicts_kept(acceptance_run):
    reply = {
        "summary": "s",
        "findings": [],
        "likely_causes": [],
        "procedure": [],
        "recommended_actions": [],
        "action_assessments": [
            {
                "step": 1,
                "action": "Continue operation and monitor vibration trend for 24 hours",
                "verdict": "consistent",
                "document_guidance": None,
                "citation_keys": [],
                "explanation": "looks fine",
            }
        ],
        "gaps": [],
    }
    answer = await compose_answer(ACCEPTANCE, acceptance_run, llm=FakeLlm(reply=reply))
    verdicts = {a.step: a.verdict for a in answer.action_assessments}
    assert verdicts[1] == "conflict"  # rule-based check found a conflict: the conservative verdict wins
    assert set(verdicts) == {1, 2, 3}  # actions the LLM skipped are filled in


async def test_llm_conflict_without_document_text_keeps_the_rule_checks_quote(acceptance_run):
    """Seen with an OpenRouter model: verdict 'conflict' but no document text, so the warning ended in 'say:'."""
    reply = {
        "summary": "s",
        "findings": [],
        "likely_causes": [],
        "procedure": [],
        "recommended_actions": [],
        "action_assessments": [
            {
                "step": 1,
                "action": "Continue operation and monitor vibration trend for 24 hours",
                "verdict": "conflict",
                "document_guidance": None,
                "citation_keys": [],
                "explanation": "the procedure says to change over",
            }
        ],
        "gaps": [],
    }
    answer = await compose_answer(ACCEPTANCE, acceptance_run, llm=FakeLlm(reply=reply))
    conflict = next(a for a in answer.action_assessments if a.step == 1)
    assert conflict.verdict == "conflict"
    assert conflict.explanation == "the procedure says to change over"  # the model's own reasoning is kept
    assert "7.1 mm/s" in conflict.document_guidance or "standby" in conflict.document_guidance
    assert conflict.citation_keys


async def test_an_action_the_documents_conflict_with_is_never_recommended(acceptance_run):
    """Seen with a free OpenRouter model: it listed the API's 'keep running' advice under what to do."""
    keep_running = "Continue operation and monitor vibration trend for 24 hours"
    reply = {
        "summary": "s",
        "findings": [],
        "likely_causes": [],
        "procedure": [],
        "recommended_actions": [
            {"text": f"API action 1 - {keep_running}", "citations": ["T7"]},
            {"text": f"Follow SOP-BFP-001 instead of the API's '{keep_running}'.", "citations": ["S1"]},
            {"text": "Change over to the standby pump.", "citations": ["S2"]},
        ],
        "action_assessments": [],
        "gaps": [],
    }
    answer = await compose_answer(ACCEPTANCE, acceptance_run, llm=FakeLlm(reply=reply))
    texts = [s.text for s in answer.recommended_actions]
    assert f"API action 1 - {keep_running}" not in texts  # the conflicting advice itself is removed
    assert f"Follow SOP-BFP-001 instead of the API's '{keep_running}'." in texts  # mentioning it to reject it is fine
    assert "Change over to the standby pump." in texts
    assert answer.grounding.removed_statements == 1


async def test_llm_failure_degrades_to_template(acceptance_run):
    answer = await compose_answer(ACCEPTANCE, acceptance_run, llm=FakeLlm(error=LlmError("timeout")))
    assert answer.grounding.mode == "template"
    assert answer.grounding.degraded is True
    assert "timeout" in answer.grounding.degraded_reason
    assert answer.recommended_actions


def test_llm_answer_schema_rejects_garbage():
    with pytest.raises(ValueError):
        LlmAnswer.model_validate({"summary": 5})


# ----------------------------------------------------------------- weak evidence


async def test_unrelated_question_admits_there_is_no_evidence(mcp, retriever):
    u = understand_with_rules("chocolate cake recipe")
    run = await investigate(u, mcp, retriever, ConversationState(), trace_id="t")
    answer = await compose_answer("chocolate cake recipe", run, llm=None)
    assert answer.citations == []
    assert answer.procedure == []
    assert any("no relevant" in g.lower() for g in answer.gaps)


async def test_partial_failure_is_reported_in_gaps(retriever):
    healthy = simulator_transport()

    async def handler(request):
        if request.url.path == "/alarms/correlation":
            return httpx.Response(503, json={"error": {"code": "unavailable", "message": "down"}})
        return await healthy.handle_async_request(request)

    async with McpToolClient(in_memory_connector(make_server(httpx.MockTransport(handler), max_retries=0))) as mcp:
        await mcp.discover()
        run = await investigate(understand_with_rules(ACCEPTANCE), mcp, retriever, ConversationState(), trace_id="t")
    answer = await compose_answer(ACCEPTANCE, run, llm=None)
    assert any("correlate_alarms" in g for g in answer.gaps)
    assert answer.recommended_actions  # still useful
