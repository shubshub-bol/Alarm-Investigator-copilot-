"""Evidence comparison and grounded answers: tool facts [T#], document citations [S#], conflicts, degradation."""

import httpx
import pytest

from copilot.answer import LlmReply, compose_answer
from copilot.consistency import assess_actions, sentences
from copilot.investigation import ConversationState, investigate
from copilot.llm import LlmError
from copilot.mcp_client import McpToolClient, http_connector, in_memory_connector
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


async def answer_offline(mcp, retriever, *questions):
    """Ask the questions in one chat and compose the last answer without a model."""
    state = ConversationState()
    for question in questions:
        run = await investigate(understand_with_rules(question), mcp, retriever, state, trace_id="t")
    return await compose_answer(questions[-1], run, llm=None)


COMPRESSOR_PROCEDURE = "Which procedure applies to Gas Compressor C-301 discharge pressure alarms?"


async def test_procedure_items_quote_the_step_to_take(acceptance_run):
    answer = await compose_answer(ACCEPTANCE, acceptance_run, llm=None)
    steps = [s.text for s in answer.procedure if s.text.startswith("SOP-BFP-001 §4.1")]
    assert any("start the standby feed pump, transfer the load to it, and stop the affected pump" in s for s in steps)


async def test_the_first_action_is_the_procedures_step(mcp, retriever):
    answer = await answer_offline(mcp, retriever, COMPRESSOR_PROCEDURE)
    first = answer.recommended_actions[0]
    assert first.text == "Follow SOP-CMP-004 §3.1: Open the recycle valve to bring discharge pressure below 42 bar."
    assert first.citations == [key_for(answer, "SOP-CMP-004 §3.1")]


async def test_plain_summary_for_a_procedure_question_names_the_section_and_its_step(mcp, retriever):
    answer = await answer_offline(mcp, retriever, COMPRESSOR_PROCEDURE)
    key = key_for(answer, "SOP-CMP-004 §3.1")
    assert answer.summary == (
        "SOP-CMP-004 §3.1 (Gas Compressor Operation and Alarm Response) applies: Open the recycle valve to bring "
        f"discharge pressure below 42 bar [{key}]."
    )


async def test_plain_summary_for_an_advice_check_counts_the_verdicts(mcp, retriever):
    answer = await answer_offline(
        mcp, retriever, ACCEPTANCE, "Are the API recommendations consistent with the maintenance manual?"
    )
    assert (
        "Of the Alarm API's 3 recommended actions, 2 match the documents, 1 conflict with them, 0 not covered"
        in answer.summary
    )


async def test_plain_summary_for_related_assets_names_those_with_recent_alarms(mcp, retriever):
    answer = await answer_offline(mcp, retriever, "What related assets should be inspected for this motor trip alarm?")
    assert (
        "Related assets with recent alarms: Recycle Compressor C-502 (1 in the 7 days before), "
        "Motor M-502 (Cooling Water Pump) (3 in the 7 days before)" in answer.summary
    )
    assert f"[{key_for(answer, 'TG-MTR-003 §6')}]" in answer.summary
    assert "(e.g. C-502); What to check: Surge events, anti-surge valve response time" in answer.summary  # a whole row


async def test_plain_summary_for_a_document_question_quotes_the_sentence_that_answers_it(mcp, retriever):
    answer = await answer_offline(mcp, retriever, "How quickly must a P1 alarm be responded to?")
    assert answer.summary.startswith("ALM-PHIL-006 §2")
    assert "5 minutes" in answer.summary


async def test_plain_summary_for_the_ai_question_quotes_the_governing_rule(mcp, retriever):
    question = "When an AI recommendation disagrees with the operating procedure, which one wins?"
    answer = await answer_offline(mcp, retriever, question)
    assert answer.summary.startswith("ALM-PHIL-006 §7")
    assert "the approved procedure governs" in answer.summary


async def test_plain_summary_says_when_nothing_is_active(mcp, retriever):
    question = "Are there active alarms on Boiler Feed Pump 101 right now?"
    answer = await answer_offline(mcp, retriever, ACCEPTANCE, question)
    assert answer.summary == "There are no active alarms on Boiler Feed Pump 101."


async def test_plain_summary_says_when_no_procedure_applies(mcp, retriever):
    answer = await answer_offline(mcp, retriever, "Which procedure applies to Boiler B-201 alarms?")
    assert answer.summary == "The documents have no procedure for boiler alarms."


async def test_the_first_action_comes_from_the_section_about_the_alarm(mcp, retriever):
    answer = await answer_offline(mcp, retriever, "Investigate Feedwater Control Valve FV-101")
    assert answer.recommended_actions[0].text.startswith("Follow TG-DEA-007 §4:")  # the valve section, not §1


async def test_no_procedure_step_comes_first_when_no_section_is_about_the_alarm(mcp, retriever):
    answer = await answer_offline(mcp, retriever, "Show active alarms for Crude Charge Pump 401")
    assert not answer.recommended_actions[0].text.startswith("Follow ")  # no section here covers seal leaks


def test_sentences_keep_abbreviations_whole():
    text = "Related asset: Driven compressor (e.g. C-502); What to check: Surge events. Next row here."
    assert sentences(text) == [
        "Related asset: Driven compressor (e.g. C-502); What to check: Surge events.",
        "Next row here.",
    ]


async def test_a_document_question_gets_no_invented_step(mcp, retriever):
    answer = await answer_offline(mcp, retriever, "How quickly must a P1 alarm be responded to?")
    assert answer.recommended_actions == []  # before: "Follow ALM-PHIL-006 §6.", a step no procedure gave


async def test_weak_documents_are_flagged_when_the_alarm_data_is_unavailable(retriever):
    question = "Investigate Fired Heater H-401"
    async with McpToolClient(http_connector("http://127.0.0.1:9/mcp", token="x", timeout_seconds=2)) as mcp:
        run = await investigate(understand_with_rules(question), mcp, retriever, ConversationState(), trace_id="t")
    answer = await compose_answer(question, run, llm=None)
    assert any("no relevant passages" in g.lower() for g in answer.gaps)


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


KEEP_RUNNING = "Continue operation and monitor vibration trend for 24 hours"


async def test_model_mode_shows_the_same_facts_as_offline_mode(acceptance_run):
    offline = await compose_answer(ACCEPTANCE, acceptance_run, llm=None)
    reply = {
        "summary": "Vibration on Boiler Feed Pump 101 is rising [T3].",
        "recommended_actions": [{"text": KEEP_RUNNING, "citations": ["T7"]}],  # not in the schema: ignored
    }
    llm = FakeLlm(reply=reply)
    answer = await compose_answer(ACCEPTANCE, acceptance_run, llm=llm)
    assert answer.grounding.mode == "llm"
    assert answer.summary == "Vibration on Boiler Feed Pump 101 is rising [T3]."
    for section in ("findings", "likely_causes", "procedure", "recommended_actions"):
        assert getattr(answer, section) == getattr(offline, section)
    assert "untrusted" in llm.prompt.lower()  # documents are passed as untrusted data


@pytest.mark.parametrize(
    ("summary", "shown"),
    [
        ("Boiler Feed Pump 101 had 24 High Vibration alarms in 90 days [T2].", None),
        ("The latest reading was 11.1 mm/s against the 7.1 mm/s limit [T5].", None),  # 11.07, rounded
        ("Vibration on BFP\u2011101 is rising [T3].", None),
        ("Vibration is rising (T3).", "Vibration is rising [T3]."),
        ("Vibration is rising [T2, T3].", "Vibration is rising [T2] [T3]."),
    ],
)
async def test_a_model_summary_that_passes_the_check_is_shown(acceptance_run, summary, shown):
    answer = await compose_answer(ACCEPTANCE, acceptance_run, llm=FakeLlm(reply={"summary": summary}))
    assert answer.summary == (shown or summary)
    assert answer.grounding.summary_source == "llm"
    assert answer.grounding.summary_rejected is None


@pytest.mark.parametrize(
    ("summary", "reason"),
    [
        ("Boiler Feed Pump 101 had 31 alarms [T2].", "number 31"),
        ("PMP-XYZ-HI alarms are rising [T2].", "code PMP-XYZ-HI"),
        ("Vibration is rising.", "no citation"),
        ("Vibration is rising [T99].", "unknown citation T99"),
        ("Vibration is rising [T3]. " + "It keeps rising. " * 40, "too long"),
    ],
)
async def test_a_model_summary_that_fails_the_check_is_replaced(acceptance_run, summary, reason):
    offline = await compose_answer(ACCEPTANCE, acceptance_run, llm=None)
    answer = await compose_answer(ACCEPTANCE, acceptance_run, llm=FakeLlm(reply={"summary": summary}))
    assert answer.summary == offline.summary
    assert answer.grounding.summary_source == "template"
    assert reason in answer.grounding.summary_rejected


async def test_each_analysis_statement_is_checked_on_its_own(acceptance_run):
    cause = "Motor M-101's bearing heats up a median 12 min before the vibration in 79% of cases: misalignment."
    first = "Start the standby pump first, as the procedure requires above 7.1 mm/s."
    reply = {
        "summary": "Vibration is rising [T3].",
        "analysis": [
            {"text": cause, "citations": ["T4", "S4"]},
            {"text": "Bearing wear explains 88% of the alarms [T4].", "citations": []},  # 88 is not in the evidence
            {"text": "Coupling misalignment is the cause.", "citations": ["S99"]},  # S99 does not exist
            {"text": f"{first[:-1]} [S2].", "citations": []},  # cited inline only
        ],
    }
    answer = await compose_answer(ACCEPTANCE, acceptance_run, llm=FakeLlm(reply=reply))
    assert [(s.text, s.citations) for s in answer.analysis] == [(cause, ["T4", "S4"]), (first, ["S2"])]
    assert answer.grounding.removed_statements == 2
    assert "S99" in answer.grounding.unknown_citations
    assert "### Analysis (AI)" in answer.markdown


async def test_analysis_citations_with_extra_words_keep_their_statement(acceptance_run):
    """Seen with an OpenRouter model: citations like 'S1 step 6'."""
    reply = {
        "summary": "Vibration is rising [T3].",
        "analysis": [
            {"text": "The procedure requires a change-over above 7.1 mm/s.", "citations": ["S1 step 6", "S1 step 7"]},
            {"text": "An invented step.", "citations": ["S99 step 2"]},
        ],
    }
    answer = await compose_answer(ACCEPTANCE, acceptance_run, llm=FakeLlm(reply=reply))
    assert [(s.text, s.citations) for s in answer.analysis] == [
        ("The procedure requires a change-over above 7.1 mm/s.", ["S1"])
    ]
    assert answer.grounding.removed_statements == 1
    assert answer.grounding.unknown_citations == ["S99"]


async def test_assessment_citations_with_extra_words_are_read(acceptance_run):
    assessment = {
        "step": 2,
        "action": "Check drive motor bearing temperature",
        "verdict": "consistent",
        "document_guidance": "Check the motor bearing temperature trend.",
        "citation_keys": ["S5 §5"],
        "explanation": "the troubleshooting guide says the same",
    }
    reply = {"summary": "s", "action_assessments": [assessment]}
    answer = await compose_answer(ACCEPTANCE, acceptance_run, llm=FakeLlm(reply=reply))
    assert next(a for a in answer.action_assessments if a.step == 2).citation_keys == ["S5"]


async def test_missing_llm_assessments_are_filled_and_conflicts_kept(acceptance_run):
    reply = {
        "summary": "s",
        "action_assessments": [
            {
                "step": 1,
                "action": KEEP_RUNNING,
                "verdict": "consistent",
                "document_guidance": None,
                "citation_keys": [],
                "explanation": "looks fine",
            }
        ],
    }
    answer = await compose_answer(ACCEPTANCE, acceptance_run, llm=FakeLlm(reply=reply))
    verdicts = {a.step: a.verdict for a in answer.action_assessments}
    assert verdicts[1] == "conflict"  # rule-based check found a conflict: the conservative verdict wins
    assert set(verdicts) == {1, 2, 3}  # actions the LLM skipped are filled in


async def test_llm_conflict_without_document_text_keeps_the_rule_checks_quote(acceptance_run):
    """Seen with an OpenRouter model: verdict 'conflict' but no document text, so the warning ended in 'say:'."""
    reply = {
        "summary": "s",
        "action_assessments": [
            {
                "step": 1,
                "action": KEEP_RUNNING,
                "verdict": "conflict",
                "document_guidance": None,
                "citation_keys": [],
                "explanation": "the procedure says to change over",
            }
        ],
    }
    answer = await compose_answer(ACCEPTANCE, acceptance_run, llm=FakeLlm(reply=reply))
    conflict = next(a for a in answer.action_assessments if a.step == 1)
    assert conflict.verdict == "conflict"
    assert conflict.explanation == "the procedure says to change over"  # the model's own reasoning is kept
    assert "7.1 mm/s" in conflict.document_guidance or "standby" in conflict.document_guidance
    assert conflict.citation_keys


async def test_analysis_never_endorses_an_action_the_documents_conflict_with(acceptance_run):
    """A 'never' elsewhere in the sentence does not turn an endorsement into a rejection."""
    endorses = f"{KEEP_RUNNING} via the API, and never leave the pump unattended."
    rejects = f"Do not {KEEP_RUNNING.lower()}; change over to the standby pump."
    unrelated = "Never leave the pump unattended."
    reply = {
        "summary": "Vibration is rising [T3].",
        "analysis": [
            {"text": endorses, "citations": ["T7"]},
            {"text": rejects, "citations": ["S1"]},
            {"text": unrelated, "citations": ["S2"]},
        ],
    }
    answer = await compose_answer(ACCEPTANCE, acceptance_run, llm=FakeLlm(reply=reply))
    assert [s.text for s in answer.analysis] == [rejects, unrelated]
    assert answer.grounding.removed_statements == 1


async def test_an_action_the_model_finds_in_conflict_is_not_recommended(acceptance_run):
    check_bearing = "Check drive motor bearing temperature"
    verdict = {
        "step": 2,
        "action": check_bearing,
        "verdict": "conflict",
        "document_guidance": None,
        "citation_keys": [],
        "explanation": "the model reads the procedure differently",
    }
    reply = {"summary": "Vibration is rising [T3].", "action_assessments": [verdict]}
    answer = await compose_answer(ACCEPTANCE, acceptance_run, llm=FakeLlm(reply=reply))
    assert not any(s.text.startswith(check_bearing) for s in answer.recommended_actions)
    assert answer.grounding.removed_statements == 1


async def test_a_model_summary_that_endorses_a_conflicting_action_is_replaced(acceptance_run):
    offline = await compose_answer(ACCEPTANCE, acceptance_run, llm=None)
    summary = f"{KEEP_RUNNING}, as the Alarm API advises [T7]."
    answer = await compose_answer(ACCEPTANCE, acceptance_run, llm=FakeLlm(reply={"summary": summary}))
    assert answer.summary == offline.summary
    assert answer.grounding.summary_source == "template"
    assert "conflict" in answer.grounding.summary_rejected


async def test_offline_answers_have_no_analysis(acceptance_run):
    answer = await compose_answer(ACCEPTANCE, acceptance_run, llm=None)
    assert answer.analysis == []
    assert "Analysis" not in answer.markdown


async def test_llm_failure_degrades_to_template(acceptance_run):
    answer = await compose_answer(ACCEPTANCE, acceptance_run, llm=FakeLlm(error=LlmError("timeout")))
    assert answer.grounding.mode == "template"
    assert answer.grounding.degraded is True
    assert "timeout" in answer.grounding.degraded_reason
    assert answer.grounding.summary_source == "template"
    assert answer.recommended_actions


def test_llm_reply_schema_rejects_garbage():
    with pytest.raises(ValueError):
        LlmReply.model_validate({"summary": 5})


# ----------------------------------------------------------------- weak evidence


async def test_unrelated_question_admits_there_is_no_evidence(mcp, retriever):
    u = understand_with_rules("chocolate cake recipe")
    run = await investigate(u, mcp, retriever, ConversationState(), trace_id="t")
    answer = await compose_answer("chocolate cake recipe", run, llm=None)
    assert answer.citations == []
    assert answer.procedure == []
    assert any("no relevant" in g.lower() for g in answer.gaps)


async def test_no_document_gap_when_no_document_search_ran(mcp, retriever):
    state = ConversationState()
    await investigate(understand_with_rules(ACCEPTANCE), mcp, retriever, state, trace_id="t1")
    question = "Are there active alarms on Boiler Feed Pump 101 right now?"
    run = await investigate(understand_with_rules(question), mcp, retriever, state, trace_id="t2")
    answer = await compose_answer(question, run, llm=None)
    assert "There are no active alarms on Boiler Feed Pump 101." in answer.gaps
    assert not any("no relevant passages" in g.lower() for g in answer.gaps)  # nothing was searched


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
