# Evidence-first answers: design

Date: 2026-10-13. Status: built on 2026-10-13.

## Goal

Answers should be complete, correct, consistent and honest every time, with or without a language model. Today
the model writes the whole answer, so its quality depends on the model's choices on the day. Real chats showed
it: a missing procedure section, a wrong number, alarms called "failures", a one-sentence answer that skipped
most of the data, and pump documents listed as the sources for a boiler question.

The principle: **the code guarantees the facts, the model does the thinking.** Copying numbers into sentences
and deciding whether the procedure is shown are not reasoning, and they are where the model's mistakes were.
Weighing the evidence, explaining the most likely cause, and saying what to do first and why are reasoning, and
the model does more of that than today, in a section of its own.

Success:

- The same question gives the same facts, procedure and steps, with or without the model.
- With the model, the answer explains the most likely cause and the first action, and why, from cited evidence.
- The answer-quality set (`python -m copilot.evaluate`) passes offline in CI, and its runs with the model are
  recorded with their pass rate.
- No answer lists documents that are not about the asset, or claims a search found nothing when none ran.

## Root causes

| Root cause | Seen as |
|---|---|
| The model decides too much: the question type, what to include, how to word the numbers | Thin answers, a missing procedure, a wrong number, "alarms" turned into "failures" |
| Document search does not know when no document fits | Pump documents for a boiler; search confidence "high" anyway |
| Only citation keys are checked, never the content | A wrong number with a real citation passes |

## Decisions

| Topic | Decision |
|---|---|
| Who writes what | Code writes every factual section from the evidence. The model writes the summary (2 to 3 sentences that answer the question), the analysis (its reasoning: the most likely cause and the first action, and why), its view of the Alarm API's advice, and optional caveats. |
| Model text | Checked before it is shown: valid keys, every number and every code or id must appear in the evidence. A summary that fails is replaced by the plain summary; an analysis statement that fails is dropped. The reasons are recorded. |
| Analysis | A new answer section, labelled as the model's analysis. It exists only with a model; offline the facts part and the plain summary carry the answer. |
| Plain summary | Built for every answer, one form per intent. Used offline, when the model fails, or when its summary fails the check. |
| Document search | Every search about an asset is limited to documents for that asset's type. Confidence comes from the playbook's main search, not the best of all searches. |
| Question routing | Rules decide the must-not-fail cases; the model decides the rest. When the model and the rules disagree, both intents are recorded. |
| Answer shape | `CopilotAnswer` keeps its fields and gains `analysis`. The GUI shows the analysis under the summary; nothing else in the GUI changes. |

## 1. Who writes each part of the answer

| Part | Written by |
|---|---|
| Summary | The model, checked (section 3); otherwise the plain summary (section 2) |
| Analysis (`analysis`, new) | The model, checked statement by statement (section 3); empty offline |
| What the data shows (`findings`) | Code: every tool fact except the Alarm API's recommendation list, as offline mode does today |
| Likely contributing factors (`likely_causes`) | Code: correlation facts, the documented pattern, the Alarm API's likely causes |
| Applicable procedure (`procedure`) | Code: the procedure passages, each with its key step (section 2) |
| Recommended actions | Code: procedure step first, then API actions by verdict, then the safety step (section 2) |
| API recommendations vs documents | The rule-based check, merged with the model's verdicts; a conflict from either side is kept, as today |
| Gaps | Code (missing data, notes, honest "no document" gaps), plus any caveats the model adds |

The model's reply schema changes from `LlmAnswer` (summary plus six lists) to `summary`, `analysis`,
`action_assessments` and `gaps`. It is still one model call per answer. The prompt keeps its current rules and
adds these:

- Answer the question that was asked, in 2 to 3 sentences.
- Analysis: 2 to 4 short statements, each citing the facts or passages it rests on: the most likely cause and
  which evidence points to it (and what argues against the others), and what to do first and why. Say how sure
  you are when the evidence is thin. Do not restate the data: the facts part already shows it.
- Keep the facts' own terms: an alarm is not a failure or a trip unless a fact says so.

Each analysis statement is a `Statement` (text and citation keys), at most 400 characters; at most 4 are kept.

## 2. The facts part, built by code

Today's template answer (`template_answer` in `answer.py`) becomes the facts part for every answer, improved in
three ways:

1. **Procedure items quote the key step.** For each procedure passage, the sentence that names an action (stop,
   start, change over, reduce, check, isolate, inspect, ...) and shares the most words with the alarm name (the
   first one on a tie); the passage snippet only if no sentence qualifies.
2. **Actions start with the procedure.** Order: the main procedure passage's key step (a conflicting API
   action is replaced by it, as today), then API actions that match the documents, then API actions the
   documents do not cover ("verify first"), then the safety step.
3. **Honest gaps.** When the main search finds no document for the asset type: "The documents have no procedure
   for boiler alarms." When no main search ran (for example, no active alarms), no "no relevant passages" gap.

Plain summary per intent:

| Intent | Plain summary |
|---|---|
| `investigate_asset` | Top alarm, count, window and trend; the leading contributing factor; any conflict with the procedure |
| `active_alarms`, `highest_priority` | Number of active alarms; the highest-priority one with its score and priority; any conflict |
| `related_assets` | The related assets with active alarms or alarms in the 7 days before (from the Alarm API's recommendation context); the documents' key sentence on what to inspect |
| `procedure_lookup` | Which section applies and its key step |
| `recommendation_check` | How many API actions match the documents, how many conflict, and which |
| `knowledge_question` | The best passage's label and first sentence |
| No evidence | "I could not find reliable evidence for this question." (as today) |

Every statement in the plain summary carries its citation keys.

## 3. The check on the model's text

Applied to the model's summary and to each analysis statement, after citation reading (`[T2]`, `[T2, S3]`,
`S1 step 6`, and now `(T2)`):

| Check | Fails when |
|---|---|
| Citations | A key does not exist, or the text cites nothing although there is evidence |
| Numbers | A number does not appear in the evidence text, compared as numbers (12 = 12.0) |
| Codes and ids | An alarm code, asset id, alarm id or document id (`PMP-VIB-HI`, `AST-BFP-101`, `ALM-000411`, `SOP-BFP-001`, `BFP-101`) does not appear in the evidence text, ignoring case and dash variants |
| Length | Summary over 600 characters; analysis statement over 400 |

The evidence text is the question, the fact statements, the passage texts and the citation labels.

A summary that fails is replaced by the plain summary. An analysis statement that fails is dropped and counted
in `removed_statements`, as an uncited statement is today. `Grounding` gains `summary_source` (`"llm"` or
`"template"`) and `summary_rejected` (the first reason, such as "number 31 is not in the evidence"). Both are
logged.

Known limit: the check catches invented numbers and codes, not a real number put in the wrong place or a weak
argument. The risk is smaller because every factual section comes from code, the model's text is short and
cited, and the analysis is labelled as the model's reasoning, next to the facts it cites.

## 4. Document search

Each search about an asset gets the asset's type as an `equipment_types` filter (already supported by
`RetrievalFilters`; documents with no types, such as the alarm philosophy, still pass it):

| Search | Document types | Equipment types |
|---|---|---|
| Procedure for an alarm | operating procedure, maintenance manual, troubleshooting guide, knowledge article | the asset's |
| Approved operating procedure | operating procedure | the asset's |
| Contributing factor | maintenance manual, troubleshooting guide, knowledge article (as today) | the asset's and the leading asset's (from the related assets) |
| Related equipment to inspect | any | the asset's |
| Check of each API action | any | the asset's |
| Safety before field inspection | safety instruction | the asset's |
| Alarm philosophy, which guidance governs | alarm philosophy | none |
| The question itself (`knowledge_question`) | any | none |

Each playbook names its main search, and the answer's `retrieval_confidence` is that search's confidence:

| Intent | Main search |
|---|---|
| `investigate_asset`, `active_alarms` | Procedure for the (top or focus) alarm |
| `highest_priority` | Alarm philosophy: what the priority means |
| `related_assets` | Related equipment to inspect |
| `procedure_lookup` | Approved operating procedure |
| `recommendation_check` | The checks of each API action; the lowest of them |
| `knowledge_question` | The question itself |

A playbook that stops before its main search (unknown asset, no alarms) reports confidence `none` without a
"no relevant passages" gap; its own gap already says why.

## 5. Question routing

Rules decide the cases that must not fail; the model decides everything else, with the rules as fallback:

- A named asset always gets its live data (built on 2026-10-13).
- "This alarm" or "this pump" after an earlier answer uses that answer's alarm or asset (exists).
- Small talk only when the message is short and names nothing from the plant (exists).

`QueryUnderstanding` gains `rules_intent`: the rules' intent when the model was used. A disagreement is then
visible in the response and the trace, and counted by the evaluation.

## 6. Evaluation

`copilot/evaluate.py` gains checks per question:

| Field | Check |
|---|---|
| `facts` | Strings that must appear in the findings, such as `"24"` and `"PMP-VIB-HI"` |
| `first_step` | The source label the first recommended action must cite, such as `"SOP-BFP-001 §4.1"` |
| `procedure` | Whether the procedure section must be present (`True`) or empty (`False`) |

And per run, with the model:

- `--repeat` asks every question twice in new chats and compares the facts part (findings, procedure, actions).
- The report counts summaries rejected by the check, analysis statements dropped, answers with an analysis, and
  model-versus-rules intent disagreements.
- `--report PATH` writes the results as Markdown, kept in `docs/evaluation/` as evidence.

A full run with the model costs about 100 requests (understanding, answer and title per chat), so it runs in
parts with `--limit` and a new `--skip` over two days on the free plan. CI runs the set offline on every push.

## 7. What does not change

The MCP server and tools, the playbooks' tool chains, the document index, and the API's response shape (new
fields only). The GUI changes in one place: the answer shows the analysis under the summary, headed "Analysis
(AI)", with citation chips like every other statement. Offline mode gives the same facts part as model mode.

## 8. Testing

Test first, as for every change: one failing test per rule (each check on the model's text, the analysis
section, each search filter, the main search confidence, each plain summary form, action order, key step, honest
gaps, `rules_intent`), then the code. A GUI test for the analysis section (shown with chips when present, absent
offline). Then the full Python suite, ruff, the GUI tests and type check, the evaluation offline, and a first
evaluation run with the model.

## 9. Documentation

`architecture.md` (who writes what), `design-decisions.md` (new decision: the code guarantees the facts, the
model does the thinking), `rag-design.md` (equipment filter, main-search confidence), `known-limitations.md` (updated
limits), and the README's sample interactions and test counts.

## Out of scope

- Checking that a real number is used in the right place (the meaning of a sentence).
- Letting the model plan its own tool calls.
- Documents for asset types that have none (boilers, fans, fired heaters): the copilot says so instead.
