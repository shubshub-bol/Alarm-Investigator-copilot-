# Hybrid agent: design

Date: 2026-10-13. Status: built on 2026-10-13. Clarified while planning: 6 recipes (not 8), the fallback
when the model fails at once, and the named-asset rule in the loop.

## Goal

Understand what the operator is really asking and answer that, in the shape the question needs. Today a
question must fit one of 8 fixed playbooks; anything else ("how many refineries do we have?", "compare pump 101
and pump 102", "which motor alarmed most last month?") falls through, and each new kind of question has needed
new code.

Success:

- Questions outside the playbooks get a direct, correct answer from the MCP tools and the documents, with no new
  code per kind of question.
- Questions the playbooks cover are answered as well as today, at about the same number of model calls.
- The answer leads with what was asked: a number for "how many", a side-by-side for "compare", the first step
  for "what should I do".
- Every fact still comes from a tool or a document and passes the existing checks; without a model the copilot
  works as today.

## Decisions

| Topic | Decision |
|---|---|
| Approach | One agent loop. Its menu is the 6 live-data playbooks (as "recipes"), the 13 MCP tools and a document search (the document playbook). A common question is one recipe step; an unusual one combines small tools. |
| Step format | Each model call returns one structured step: a recipe, a tool, a document search, or "done" with the answer. Provider-neutral (Gemini and OpenRouter), and a fake model can play it in tests. |
| Limits | At most 6 steps; read-only tools; at most one recipe per question. |
| Answer | The model names the answer type (count, list, compare, why, what to do, document, cannot answer) and writes a direct answer plus its analysis. Code builds the details and runs the existing checks. |
| Layout | The direct answer first; the details (data, analysis, procedure, steps, sources, agent steps) are one click away. |
| Without a model | The keyword rules pick a playbook, as today. A question no playbook covers gets an honest "this question needs the language model". |

## 1. Flow

```text
question + chat context
   -> agent step 1..6: model picks one action -> code checks and runs it -> result (tagged T# / S#) back to the model
   -> model says "done": answer type, direct answer, analysis, caveats
   -> code checks the model's text and builds the details
   -> answer: direct answer first, details below
```

Small talk keeps its own path: the rules recognise greetings and thanks before any model call, as today.

## 2. The agent loop (`copilot/agent.py`, new)

At each step the model gets:

- the question and the chat context (`copilot/context.py`);
- the menu: each recipe and each MCP tool with a one-line description and its inputs (taken from the tool
  schemas discovered on the MCP server), and `search_documents(query, doc_types?)`;
- the results so far, each with its key (`[T3] summarize_alarms(...) -> ...`, `[S2] SOP-BFP-001 §4.1 ...`);
- the number of steps left.

It returns one `AgentStep`:

| Field | Meaning |
|---|---|
| `action` | `recipe`, `tool`, `search_documents` or `done` |
| `name` | the recipe or tool name |
| `arguments_json` | the inputs, as a JSON object in a string (free-form objects are not accepted by every provider's structured output) |
| `why` | one sentence; shown in the trace as the step's purpose |
| `final` | with `done`: the answer (section 5) |

What the code does:

| Situation | Behaviour |
|---|---|
| Valid step | Run it; record it in the trace; give the model the tagged result |
| Unknown name, bad JSON or inputs that fail the tool's schema | Not run; the model gets the error and can correct itself (the attempt counts as a step) |
| A tool fails | The model gets the error code and message; it can try another way or answer |
| The same call again | Not run; the model gets the earlier result |
| A second recipe | Not run; the model is told to use the tools for anything more |
| The question names an asset (by the keyword rules) and no recipe or tool has run yet, but the step is a document search or "done" | Not run; the model is told to look up the asset's data first (the rule "a named asset always gets its live data" carries over) |
| Result too long | Shortened for the model: lists cut to their first 10 items plus the total count, raw `meta` blocks dropped |
| 6 steps used, model error or invalid reply | The loop stops; the answer is built from what was found (section 5) |

## 3. Recipes as tools

The 6 playbooks in `investigation.py` that read live data are offered as recipes (`investigate_asset`,
`active_alarms`, `highest_priority`, `related_assets`, `procedure_lookup`, `recommendation_check`); the document
playbook is the `search_documents` action, and small talk keeps its own path. Each recipe has the inputs it reads from
`QueryUnderstanding`: `asset` (a name or id), `asset_type`, `site`, `unit`, `lookback_days`, `severities`,
`alarm_topic`, `uses_context`. Running a recipe builds a `QueryUnderstanding` with that intent and those inputs
and runs the playbook on the agent's `Investigation`; nothing in the playbooks is rewritten.

One `Investigation` (one evidence record, one trace) serves the whole question: the recipe fills the evidence as
today; small tools go through `Investigation.tool` and document searches through `Investigation.retrieve`, so the
trace, the passage keys and the main-search confidence work as they do now. At the end the chat state is updated
from the evidence, as `Investigation.run` does.

## 4. Facts and keys

Keys are given when a result arrives, so the keys the model cites are the answer's keys:

- after a recipe, its facts are built with `build_facts` (T1, T2, ...);
- each small tool result becomes one fact with the next key: a short statement of the tool, its inputs and its
  shortened result;
- document passages keep their `S#` keys from `Investigation.retrieve`.

## 5. The final answer

`final` holds:

| Field | Meaning |
|---|---|
| `answer_type` | `count`, `list`, `compare`, `why`, `what_to_do`, `document`, `cannot_answer` |
| `direct_answer` | 1 to 3 sentences that answer the question in that shape, each ending with its keys |
| `analysis` | the model's reasoning, as today |
| `action_assessments`, `gaps` | as today |

The direct answer and the analysis go through the existing checks (keys, numbers and codes in the evidence,
length with shortening, keys added when missing, no endorsement of advice the documents conflict with). The
details are built by code: with a recipe, its facts part as today (data, causes, procedure, steps); without one,
the tool facts as findings and any retrieved passages as the procedure section.

When there is no valid final answer (step limit, model failure, or a direct answer that fails the checks): with a
recipe, its plain summary; without one, a plain summary of what was looked up, plus the gap "The question could
not be fully answered within 6 steps" or the model error. `CopilotAnswer` gains `answer_type` (`null` for plain
answers and for the rules path).

## 6. Without a model

`service.py` uses the agent only when a model is configured. Without one, the rules choose a playbook as today.
If the model fails before any step has run, the question is answered the same way and marked degraded, as a model
outage is today.
A question that names nothing the rules recognise falls back to the document search, as today; if that finds
nothing relevant, the answer adds "This question needs the language model."

## 7. GUI

The direct answer stays where the summary is. "What to do" stays on the answer for `what_to_do` and `why`, and
whenever a conflict warning is shown; for the other answer types the steps move behind the evidence links with the
rest of the details. The trace already lists every tool call; each agent step shows its `why` as the purpose.

## 8. Cost

A question a recipe covers: one step to choose the recipe and one to answer (2 model calls), plus the chat title
for a new chat. This replaces the current understanding call, so the cost is about today's. Other questions: 3 to
7 calls. The step limit bounds the worst case.

## 9. Evaluation

The answer-quality set gains questions no playbook covers, each with an expected answer type and key facts:
"How many refineries do we have?" (1, EastRefinery), "Compare Boiler Feed Pump 101 and 102 over the last 90 days" (42 and 20 alarms),
"Which motor alarmed most in the last 90 days?" (Motor M-101), "How many assets are at EastRefinery?" (7). They are marked
`model_only`: skipped offline (CI runs the set offline and must stay at 100%), run with the model in small parts
because of the free tier's daily limit, and recorded in `docs/evaluation/`. The report counts steps per question
and stops that hit the step limit.

## 10. Testing

Test first. A fake model plays scripted steps, so no test spends real requests: a recipe in one step; small tools
over several steps; each row of the table in section 2 (bad name, bad inputs, failing tool, repeated call,
second recipe, long result, step limit, model error); every answer type; the checks on the direct answer; the
fallback answers; the rules path without a model. The existing tests and the offline set keep passing.

## 11. Documentation

`architecture.md` (the agent loop and its menu), `design-decisions.md` (why one agent with recipes as tools),
`known-limitations.md` (step limit, cost per question, model dependence), and the README.

## Out of scope

- Write actions: every tool stays read-only.
- More than one recipe per question (comparisons use the small tools).
- Native function-calling APIs: the structured step format works on both providers and in tests.
