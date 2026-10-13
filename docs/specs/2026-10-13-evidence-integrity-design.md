# Evidence integrity: design

Date: 2026-10-13. Status: agreed in review (part 1 approved; the user approved the rest: "go ahead with all you
want").

## Goal

The first full run with the agent and Gemini (33 of 38 passed, `docs/evaluation/2026-10-13-agent-part*.md`) failed
for three reasons that are design gaps, not single bugs. Fix each at its cause, so the whole class of problem goes
away:

| Root cause | What went wrong | Seen as |
|---|---|---|
| 1. Evidence has holes | "Nothing found" is not evidence, so an honest negative answer has nothing to cite; and when nothing was looked up at all, uncited text passes the checks | "Any boiler alarms active?": the model's "none" was thrown away for an invented key; "AI vs procedure, which wins?" was answered from the model's memory, uncited |
| 2. The exam checks the route, not the result | Cases were written for the fixed playbooks and check which playbook ran | "Pump 101 is making noise": a good answer through another valid recipe was marked wrong |
| 3. Only playbooks fill the evidence record | The asset, alarms and summary are written by playbook code; the agent's own tool calls leave them empty | After the agent's B-201 answer, "this boiler" has no asset to refer to, and the evidence panel is empty |

Success:

- The 5 failed questions pass for the right reasons; no expectation is loosened only to make a case pass.
- No code special-cases a question.
- Without a model the copilot behaves as today (offline set 34/34), except that negative findings are now cited
  facts instead of uncited gaps.
- The 33 questions that passed still pass.

## Decisions

| Topic | Decision |
|---|---|
| Evidence record | One recorder at the single door every tool call passes through (`Investigation.call_tool`) writes what each result plainly shows. Playbooks keep only their judgements. (Option A of three; B, rebuilding the record from the trace, was too large a rewrite; C, an agent-only adapter, would be two places to keep in sync.) |
| Negative results | A lookup that succeeds and finds nothing is a fact with its own key, tied to its trace step, worded with the count ("0 active alarms"). |
| Gaps | Only what could not be looked up or failed: a service down, an error, a missing input. |
| No evidence | With nothing looked up, the only valid answer is "cannot answer". The agent is told so before it may answer, and the answer checks enforce it. |
| Evaluation | Checks outcomes the operator sees. The route (intent) is checked only on the rules path, where routing is what is being tested. |

## 1. One evidence record (`copilot/evidence.py`, new)

After each successful call, `call_tool` passes the record to `record(evidence, tool, arguments, result)`, one rule
per tool, writing the same shapes the playbooks write today (so facts, the GUI and answers keep working):

| Tool | Records |
|---|---|
| `search_assets` | every asset found, in a list of known assets (`Evidence.known_assets`, by asset id) |
| `get_asset_metadata` | the asset (the fields the asset fact uses) and its related assets |
| `get_alarm_details` | the alarm, and its asset as known |
| `get_alarms` with `status=active` | the active alarms |
| `summarize_alarms` | the alarm summary |
| `get_alarm_trends` | the trend |
| `score_alarm_priority` | the priority |
| `get_operator_recommendations` | the API's advice, and the asset it names as known |
| `correlate_alarms` | the correlation pairs (the playbook still chooses which are strong enough) |
| floods, rationalization candidates, KPI tools | nothing beyond their fact |

The asset of the question (`Evidence.asset`) is set when the calls clearly focus on one asset: asset metadata, an
alarm's details, the API's advice, or a call filtered to exactly one asset id that is in `known_assets`. A search
with several results never sets it. A playbook that has already chosen the asset keeps its choice.

Playbooks stop copying tool results into the record; they keep their judgements: the top alarm, the focus alarm,
the ranking, which contributing factors are strong enough, the notes. Agent runs that call tools directly now fill
the evidence panel and the chat memory, so a follow-up ("this boiler") works.

## 2. No holes in the evidence

### 2a. Nothing found is a fact

- **Agent tool calls.** A successful result whose main list is empty is stated in words with the count, before
  the shortened data: `get_alarms(status="active", asset_id="AST-BLR-201") found 0 alarms.`
- **Playbooks.** Every negative finding a playbook writes after a lookup is recorded with the step that
  established it and becomes a fact (`T#`) in step order, for example "There are 0 active alarms on boiler assets
  (Boiler B-201).", "No asset matches 'Boiler Feed Pump 999'.", "There are 0 high/critical alarms on X in the last
  90 days.", "No alarm on X often fires first." Findings about alarms state the count as the number 0 (not "no"),
  so a model's "0 active alarms" is in the evidence.
- **Document searches.** A search that runs and finds nothing relevant is a fact too, tied to its retrieval step
  ("The documents have no procedure for boiler alarms."). A search that could not run (no index, an error) stays a
  gap.
- The agent's document search with no result answers the model with that fact's key, so it can cite it.
- The plain (template) summary cites the negative fact it uses.

### 2b. No evidence, no claim

- **Agent.** "done" with an answer type other than `cannot_answer`, when no lookup has run (no tool call that
  reached the server, no document search), is refused once: "Nothing has been looked up yet: search the documents
  or call a tool, or answer cannot_answer." The named-asset rule stays as the stricter case.
- **Checks.** A model's direct answer with no evidence behind the whole answer (no facts and no passages) is
  rejected unless its type is `cannot_answer`; an honest "I cannot answer that" needs no key.

## 3. The exam judges outcomes (`copilot/evaluate.py`)

- **Rules path (no model):** the intent is checked, as now; it is what the rules are for.
- **With a model:** the intent is not checked. Every other expectation is an outcome: the asset the answer is
  about, live data used or not, values in the direct answer or findings (whole-number patterns), sources, the
  first step's source, a procedure when one applies, the honest negative facts, and every key valid.
- Every case's expectations are reviewed against "what must the operator see for this question". Route-only
  expectations become outcomes or go:
  - "Boiler Feed Pump 101 is making noise, what should I do?" keeps its sources, first step and procedure; the
    recipe it runs is not checked with a model.
  - "Boiler B-201 how many times it failed in past?": the asset AST-BLR-201, live data, and its alarm count 24.
    "No procedure for boiler alarms" is not something this question asks, so it is not expected.
  - Negative findings that were expected as gaps ("no active alarms on boiler assets", "No asset matches") are
    expected as facts.
- The report lists each answer and its tools (built already), and also the document searches.
- Afterwards the whole set (38) is run with the model and recorded in `docs/evaluation/`.

## 4. What the operator sees

- "What the data shows" includes the negative facts, cited ("0 active alarms on Boiler B-201 [T2]").
- "Gaps" lists only what could not be looked up or failed.
- The evidence panel shows the asset and alarms the agent's tools found.

## 5. Testing

Test first. One test per recorder rule; an agent tools-only run records its asset; negative facts from each kind of
lookup (agent tool, playbook, document search) are keyed and citable; the plain summary cites its negative fact; a
model answer of "0 active alarms [T#]" passes the number check; "done" with nothing looked up is refused unless it
is `cannot_answer`; an uncited answer with no evidence is rejected unless it is `cannot_answer`; the exam checks the
intent only without a model. The offline set stays 34/34 (its negative expectations move from gaps to facts); the
full suite, ruff and mypy (`uv run python -m mypy`) pass; then the 38 questions run with Gemini.

## 6. Documentation

`architecture.md` (the recorder, negative facts, what gaps are), `design-decisions.md` (a section: evidence
integrity), `known-limitations.md`, the README's sample answer for "no active alarms" if its wording changes, and
the test counts.

## Out of scope

- Turning raw tool data in agent facts into prose (the findings still show shortened data for agent tool calls).
- Changing which recipes exist or how the agent chooses steps.
- A larger document corpus.
