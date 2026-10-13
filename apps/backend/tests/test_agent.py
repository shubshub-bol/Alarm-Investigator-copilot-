"""The agent loop: model-chosen steps over the recipes, the MCP tools and the document search."""

import json

import httpx
import pytest

from copilot.agent import MAX_STEPS, RESULT_CHARS, Agent, tool_line
from copilot.investigation import MAX_PASSAGES, ConversationState
from copilot.llm import LlmError
from copilot.mcp_client import McpToolClient, in_memory_connector
from copilot_test_helpers import simulator_transport
from mcp_test_helpers import make_server


class ScriptedLlm:
    """Plays the model: returns the given steps in order and keeps every prompt."""

    name = "scripted"

    def __init__(self, *steps):
        self.steps, self.prompts = list(steps), []

    async def generate_json(self, system, prompt, schema):
        self.prompts.append(prompt)
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return schema.model_validate(step)

    async def generate_text(self, system, prompt, temperature=None):
        return "Boiler count"


def tool(name, why="", **args):
    return {"action": "tool", "name": name, "arguments_json": json.dumps(args), "why": why}


def recipe(name, **args):
    return {"action": "recipe", "name": name, "arguments_json": json.dumps(args)}


def search(query, **args):
    return {"action": "search_documents", "arguments_json": json.dumps({"query": query, **args})}


def done(text, answer_type="count"):
    return {"action": "done", "final": {"answer_type": answer_type, "direct_answer": text}}


async def run_agent(question, *steps, mcp, retriever, context="", state=None):
    llm = ScriptedLlm(*steps)
    agent = Agent(question, context, llm, mcp, await mcp.discover(), retriever, state or ConversationState(), "t")
    return await agent.run(), llm


async def test_a_tool_step_runs_and_its_result_is_keyed(mcp, retriever):
    result, llm = await run_agent(
        "How many boilers do we have?",
        tool("search_assets", "find the boilers", query="boiler", asset_type="boiler"),
        done("Boiler B-201 is the only boiler [T1]."),
        mcp=mcp,
        retriever=retriever,
    )
    assert result.final.direct_answer == "Boiler B-201 is the only boiler [T1]."
    assert [(f.key, f.tool) for f in result.facts] == [("T1", "search_assets")]
    assert "Boiler B-201" in result.facts[0].statement
    assert "[T1] search_assets(" in llm.prompts[1] and f"STEPS LEFT: {MAX_STEPS - 1}" in llm.prompts[1]
    assert result.run.steps[0].purpose == "find the boilers"
    assert (result.understanding.intent, result.steps) == ("agent", 2)


async def test_the_model_sees_the_question_the_chat_and_the_menu(mcp, retriever):
    _, llm = await run_agent(
        "How many boilers do we have?",
        done("No data was looked up.", "cannot_answer"),
        mcp=mcp,
        retriever=retriever,
        context="User: hello",
    )
    first = llm.prompts[0]
    assert "QUESTION: How many boilers do we have?" in first
    assert "EARLIER IN THIS CHAT" in first and "User: hello" in first
    assert "- investigate_asset:" in first and "- summarize_alarms:" in first and "- search_documents:" in first


async def test_a_document_search_adds_keyed_passages(mcp, retriever):
    result, llm = await run_agent(
        "What must be done before inspecting a pump?",
        search("lockout and isolation before inspecting a pump", doc_types=["safety_instruction"]),
        done("Lock out and isolate first [S1].", "document"),
        mcp=mcp,
        retriever=retriever,
    )
    labels = [p.citation.label for p in result.run.evidence.passages]
    assert labels and all(label.startswith("SAF-") for label in labels)  # only safety instructions were searched
    assert "[S1] SAF-" in llm.prompts[1]
    assert result.run.evidence.retrieval_confidence is not None  # a main search: it sets the answer's confidence


async def test_a_recipe_runs_its_playbook_and_its_facts_continue_the_keys(mcp, retriever):
    state = ConversationState()
    result, llm = await run_agent(
        "Investigate Boiler Feed Pump 101",
        tool("get_asset_metadata", asset_id="AST-BFP-101"),
        recipe("investigate_asset", asset="Boiler Feed Pump 101", lookback_days=90, severities=["high", "critical"]),
        done("Vibration is rising [T4].", "why"),
        mcp=mcp,
        retriever=retriever,
        state=state,
    )
    ev = result.run.evidence
    assert result.understanding.intent == ev.intent == "investigate_asset"
    assert ev.asset["asset_id"] == "AST-BFP-101"
    assert [f.key for f in result.facts] == [f"T{n}" for n in range(1, len(result.facts) + 1)]
    assert (result.facts[0].tool, result.facts[0].step) == ("get_asset_metadata", 1)
    assert result.facts[1].tool == "get_asset_metadata"
    assert all(f.step > 1 for f in result.facts[1:])  # the recipe's facts come from the recipe's own steps
    assert "SOP-BFP-001 §4.1" in {p.citation.label for p in ev.passages}
    assert "[T2] Boiler Feed Pump 101 (AST-BFP-101)" in llm.prompts[2]
    assert state.last_asset["asset_id"] == "AST-BFP-101"


async def test_a_type_word_given_to_a_recipe_becomes_its_type(mcp, retriever):
    result, _ = await run_agent(
        "any boiler alarms active?",
        recipe("active_alarms", asset="boilers"),
        done("No boiler alarm is active.", "list"),
        mcp=mcp,
        retriever=retriever,
    )
    assert (result.understanding.asset_query, result.understanding.asset_type) == (None, "boiler")
    assert result.run.steps[0].arguments.get("asset_type") == "boiler"


async def test_a_second_recipe_is_not_run(mcp, retriever):
    result, llm = await run_agent(
        "Investigate Boiler Feed Pump 101",
        recipe("investigate_asset", asset="Boiler Feed Pump 101"),
        recipe("active_alarms", asset="Boiler Feed Pump 102"),
        done("Vibration is rising [T3].", "why"),
        mcp=mcp,
        retriever=retriever,
    )
    assert "Not run: one recipe per question" in llm.prompts[2]
    assert result.understanding.intent == "investigate_asset"


@pytest.mark.parametrize(
    ("step", "reply"),
    [
        (
            {"action": "tool", "name": "get_alarms", "arguments_json": "not json"},
            "Not run: arguments_json is not valid",
        ),
        ({"action": "tool", "name": "get_alarms", "arguments_json": "[1, 2]"}, "Not run: arguments_json must be"),
        (tool("delete_alarms"), "Not run: there is no tool 'delete_alarms'"),
        (recipe("fix_pump"), "Not run: there is no recipe 'fix_pump'"),
        (recipe("investigate_asset", lookback_days=5000), "Not run: the recipe's inputs are invalid"),
        (tool("get_alarm_details"), "Failed: [INVALID_INPUT]"),  # alarm_id is required
        (search(""), "Not run: search_documents needs a 'query'"),
        (
            recipe("investigate_asset", asset_name="Boiler Feed Pump 101"),
            "Not run: unknown recipe input(s) asset_name; inputs: asset, asset_type, site",
        ),
    ],
)
async def test_bad_steps_are_reported_and_not_run(step, reply, mcp, retriever):
    _, llm = await run_agent(
        "Any alarms?", step, done("Nothing was found.", "cannot_answer"), mcp=mcp, retriever=retriever
    )
    assert reply in llm.prompts[1]


async def test_a_tool_call_with_bad_inputs_leaves_no_gap_in_the_answer(mcp, retriever):
    result, _ = await run_agent(
        "Any alarms?",
        tool("get_alarm_details"),
        done("Nothing was found.", "cannot_answer"),
        mcp=mcp,
        retriever=retriever,
    )
    assert result.run.steps[0].error_code == "INVALID_INPUT"  # shown in the trace
    assert result.run.evidence.notes == []  # but the model's slip is not a gap in the data


async def test_a_failing_tool_is_reported_to_the_model(retriever):
    healthy = simulator_transport()

    async def handler(request):
        if request.url.path == "/alarms/summary":
            return httpx.Response(503, json={"error": {"code": "unavailable", "message": "down"}})
        return await healthy.handle_async_request(request)

    async with McpToolClient(in_memory_connector(make_server(httpx.MockTransport(handler), max_retries=0))) as mcp:
        await mcp.discover()
        _, llm = await run_agent(
            "How many alarms did Boiler B-201 have?",
            tool("summarize_alarms", asset_ids=["AST-BLR-201"]),
            done("The alarm summary is unavailable.", "cannot_answer"),
            mcp=mcp,
            retriever=retriever,
        )
    assert "Failed: [UPSTREAM_UNAVAILABLE]" in llm.prompts[1]


async def test_tools_are_not_called_once_the_mcp_server_is_down(mcp, retriever):
    llm = ScriptedLlm(tool("search_assets", query="boiler"), done("Nothing was found.", "cannot_answer"))
    agent = Agent("How many boilers?", "", llm, mcp, await mcp.discover(), retriever, ConversationState(), "t")
    agent.inv.mcp_down = True
    result = await agent.run()
    assert "Failed: the MCP server is unavailable." in llm.prompts[1]
    assert result.run.steps == []


async def test_the_same_call_is_not_run_twice(mcp, retriever):
    result, llm = await run_agent(
        "How many boilers do we have?",
        tool("search_assets", query="boiler", asset_type="boiler"),
        tool("search_assets", query="boiler", asset_type="boiler"),
        done("Boiler B-201 is the only boiler [T1]."),
        mcp=mcp,
        retriever=retriever,
    )
    assert "Already done in an earlier step" in llm.prompts[2]
    assert len(result.run.steps) == 1


async def test_a_long_result_is_shortened_for_the_model(mcp, retriever):
    result, _ = await run_agent(
        "List every alarm at NorthPlant",
        tool("get_alarms", site="NorthPlant", fetch_all=True, max_items=200),
        done("NorthPlant has many alarms [T1].", "list"),
        mcp=mcp,
        retriever=retriever,
    )
    statement = result.facts[0].statement
    assert "more (" in statement and "in all)" in statement
    assert len(statement) <= RESULT_CHARS + 200


async def test_the_loop_stops_after_six_steps(mcp, retriever):
    steps = [tool("search_assets", query=f"pump {n}") for n in range(MAX_STEPS)]
    result, llm = await run_agent("Find every pump", *steps, mcp=mcp, retriever=retriever)
    assert (result.final, result.hit_limit, result.steps, len(llm.prompts)) == (None, True, MAX_STEPS, MAX_STEPS)


async def test_a_model_failure_before_any_step_is_marked(mcp, retriever):
    result, _ = await run_agent("Any alarms?", LlmError("429 RESOURCE_EXHAUSTED"), mcp=mcp, retriever=retriever)
    assert result.failed_at_once and "429" in result.model_error


async def test_a_model_failure_after_a_step_keeps_what_was_found(mcp, retriever):
    result, _ = await run_agent(
        "How many boilers do we have?",
        tool("search_assets", query="boiler", asset_type="boiler"),
        LlmError("timeout"),
        mcp=mcp,
        retriever=retriever,
    )
    assert not result.failed_at_once and result.model_error == "timeout" and len(result.facts) == 1


async def test_a_named_asset_must_get_its_data_before_the_answer(mcp, retriever):
    result, llm = await run_agent(
        "Boiler B-201 how many times it failed in past?",
        done("The documents do not say.", "cannot_answer"),
        search("boiler failures"),
        recipe("investigate_asset", asset="Boiler B-201"),
        done("Boiler B-201 had 24 alarms in the last 90 days [T2].", "count"),
        mcp=mcp,
        retriever=retriever,
    )
    assert "look up its data first" in llm.prompts[1] and "look up its data first" in llm.prompts[2]
    assert result.understanding.intent == "investigate_asset" and result.final.answer_type == "count"


async def test_done_without_a_final_answer_is_not_accepted(mcp, retriever):
    result, llm = await run_agent(
        "How many boilers do we have?",
        {"action": "done"},
        done("Nothing was looked up.", "cannot_answer"),
        mcp=mcp,
        retriever=retriever,
    )
    assert "'done' needs the final answer" in llm.prompts[1] and result.final is not None


# ---------------------------------------------------------------- fix round 1
async def test_asset_id_is_accepted_as_the_asset_of_a_recipe(mcp, retriever):
    result, _ = await run_agent(
        "Investigate Boiler Feed Pump 102",
        recipe("investigate_asset", asset_id="AST-BFP-102"),
        done("Boiler Feed Pump 102 was investigated [T1].", "why"),
        mcp=mcp,
        retriever=retriever,
    )
    assert result.understanding.asset_query == "AST-BFP-102"
    assert result.run.evidence.asset["asset_id"] == "AST-BFP-102"  # not the pump the topic search would pick


async def test_a_recipe_with_unknown_inputs_is_not_run(mcp, retriever):
    result, llm = await run_agent(
        "Any alarms?",
        recipe("active_alarms", site="NorthPlant", zone="A", area="B"),
        done("Nothing was found.", "cannot_answer"),
        mcp=mcp,
        retriever=retriever,
    )
    assert "Not run: unknown recipe input(s) area, zone; inputs: " in llm.prompts[1]
    assert (result.run.steps, result.understanding.intent) == ([], "agent")  # nothing ran, no recipe is used up


async def test_a_tool_call_rejected_by_its_schema_is_not_a_lookup(mcp, retriever):
    result, llm = await run_agent(
        "Boiler B-201 how many times it failed in past?",
        tool("get_alarm_details"),  # alarm_id is required: the call never reaches the server
        done("The data could not be read.", "cannot_answer"),
        recipe("investigate_asset", asset="Boiler B-201"),
        done("Boiler B-201 had 24 alarms in the last 90 days [T2].", "count"),
        mcp=mcp,
        retriever=retriever,
    )
    assert "look up its data first" in llm.prompts[2]
    assert result.understanding.intent == "investigate_asset" and result.final.answer_type == "count"


async def test_a_search_past_the_evidence_limit_says_so(mcp, retriever):
    result, llm = await run_agent(
        "Investigate Boiler Feed Pump 101",
        recipe("investigate_asset", asset="Boiler Feed Pump 101"),
        search("lockout and isolation before inspecting a pump", doc_types=["safety_instruction"]),
        search("how alarm priorities are set", doc_types=["alarm_philosophy"]),
        search("bearing inspection and maintenance", doc_types=["maintenance_manual"]),
        search("starting and stopping a pump", doc_types=["operating_procedure"]),
        done("Vibration is rising [T4].", "why"),
        mcp=mcp,
        retriever=retriever,
    )
    assert len(result.run.evidence.passages) == MAX_PASSAGES  # the limit was reached
    assert "not kept" in llm.prompts[-1] and f"the evidence limit ({MAX_PASSAGES} passages)" in llm.prompts[-1]
    assert "No relevant passages." not in llm.prompts[-1]  # passages were found: only the limit stopped them


async def test_the_menu_lines_are_whole_sentences_on_one_line(mcp):
    lines = {t.name: tool_line(t) for t in await mcp.discover()}
    assert lines and all("\n" not in line for line in lines.values())
    assert "to asset ids, best match first. Inputs: " in lines["search_assets"]  # not cut at "e.g."
    assert "grouped e.g. by alarm_name. Inputs: " in lines["summarize_alarms"]
    assert "ISA-18.2 uses 10 in 10) and the alarm that initiated each flood. Inputs: " in lines["analyze_alarm_floods"]


async def test_a_named_asset_does_not_wait_for_a_lookup_that_has_no_tools(mcp, retriever):
    llm = ScriptedLlm(done("No data could be looked up.", "cannot_answer"), done("Not reached.", "cannot_answer"))
    question = "Boiler B-201 how many times it failed in past?"
    agent = Agent(question, "", llm, mcp, [], retriever, ConversationState(), "t")  # no tools were discovered
    result = await agent.run()
    assert (result.final.direct_answer, result.steps, len(llm.prompts)) == ("No data could be looked up.", 1, 1)
