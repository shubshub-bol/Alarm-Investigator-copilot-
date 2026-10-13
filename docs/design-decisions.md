# Design decisions

The main choices, what they cost, and why they were made.

## 1. Build the Alarm Management API as a simulator, contract first

The assignment provides the API only as Postman collections. The simulator implements all 15 endpoints from
them and is checked against the collections (47 requests pass). Its data is generated with a fixed seed and has
patterns planted on purpose (recurring vibration preceded by a hot motor bearing, a flood, a deliberate conflict
between the API's advice and a procedure), so the copilot has real findings to discover and every test is
repeatable.

*Cost:* the data is synthetic and fixed in time. *Instead of:* returning canned JSON, which would make chaining,
filters and analytics meaningless.

## 2. One MCP server with fine-grained, typed, read-only tools

Each tool wraps one endpoint (`calculate_kpi` hides the generate-then-execute pair), has pydantic input and
output schemas, and is annotated read-only and idempotent. Fine-grained tools are reusable by any MCP client,
make every chained call visible in the trace, and keep the server a thin, testable adapter; the investigation
logic lives in the copilot.

*Cost:* more tools to document and more calls per question. *Instead of:* one coarse `investigate_asset` tool,
which would hide the chaining the assignment asks to see and could not be reused.

## 3. The connector is a separate library

`connectors/alarm_api_client` owns HTTP concerns (auth, timeouts, retries, paging, typed errors) and knows
nothing about MCP. It is tested on its own with a mock transport and against the simulator, and could serve any
other Python service.

## 4. Errors as stable codes in tool results

A failed tool returns an MCP tool error whose text starts with a code (`[NOT_FOUND]`, `[UPSTREAM_UNAVAILABLE]`,
...) and says how to recover. Language models and people can both read it, the GUI can show it, and the copilot
can branch on it. Protocol-level failures are reserved for real protocol problems.

## 5. Deterministic playbooks instead of free-form tool calling

The language model reads the question (intent and entities) and writes the answer; the tool chain in between
is a playbook per intent, written in code. This gives the right tools in the right order every time, a
predictable number of model calls (two or three per question), predictable latency and cost, chains that can be
tested, and an answer even with the model switched off.

The playbooks are not scripts for the sample questions: they take any asset, asset type, site, unit, time window
and severity, resolve names through MCP, pick the asset with the most matching alarms when only a type is given,
and use follow-up context ("this alarm"). Eight intents cover the use case.

*Cost:* a new kind of question needs a new playbook. *Instead of:* letting the model choose tools in a loop,
which is flexible but harder to test, slower, and can skip steps such as the safety search.

*Later change:* [decision 19](#19-one-agent-with-the-playbooks-as-recipes) lets the model choose between these
playbooks (as recipes) and the small tools. The playbooks themselves are unchanged, and they are still what runs
without a model.

## 6. A swappable language model, and a copilot that works without one

The copilot depends only on an `LlmProvider` protocol (structured JSON and plain text). Gemini and OpenRouter
are implemented; `LLM_PROVIDER=offline` uses keyword rules and template answers. Outputs are validated against
JSON schemas, transient provider errors are retried, and any failure falls back instead of failing the question.
An evaluator without an API key still gets a complete, cited answer.

## 7. Grounding enforced after generation

Tool results become numbered facts `[T#]` and passages become `[S#]`. The model must cite them, and the answer is
checked afterwards: unknown keys are removed and uncited findings or actions are dropped, with a count kept in
the response. Prompting alone cannot guarantee grounding; validation can.

## 8. Conflicts between the API's advice and the procedures are decided in favour of safety

Every API recommendation is compared with the retrieved passages by a rule-based check and by the model. If
either finds a conflict, the conflict is shown ("Check before acting"), with the procedure quoted and the
governing rule (`ALM-PHIL-006 §7`: the approved procedure governs) retrieved and cited. A conflicting API action
is also removed from the recommended actions if the model lists it anyway (seen with a free model). Missing a
conflict is worse than flagging one too many.

## 9. Hybrid retrieval with a small local model and no vector database

BM25 finds exact alarm codes and part numbers; dense vectors find meaning; Reciprocal Rank Fusion combines them
without score calibration. `bge-small-en-v1.5` on ONNX Runtime runs on a CPU in milliseconds and needs no API key.
With 54 passages, an in-memory exact search is faster and simpler than a vector database. Confidence thresholds
were measured on this corpus. Details: [rag-design.md](rag-design.md).

*Cost:* the index is rebuilt in full when documents change, and a much larger corpus would need a vector store.

## 10. Documents are untrusted input

Prompt-injection protection is layered: screening and quarantine at ingestion, removal of hidden text,
untrusted-content wrapping in the prompt, citation validation, and read-only tools. A planted injection in the
corpus proves the screening works.

## 11. Stream the real steps

Investigations take seconds (10 to 25 with a model). The backend streams each real step as a Server-Sent Event
while it happens, so the GUI shows honest progress and the full trace, not a spinner. SSE is plain HTTP, which
nginx and proxies handle without extra setup.

## 12. SQLite for saved chats

Chats persist across reloads and restarts in one SQLite file (a Docker volume): standard library, transactions,
no extra service. Each answer is saved in one transaction with the follow-up context, so a crash never leaves
half a chat.

*Cost:* one writer at a time, which suits a single-user tool.

## 13. A calm operator interface

The GUI follows the ISA-101 idea from control-room screens: neutral greys, colour only for abnormal states,
priority shown by colour, shape and text together. The layout is a familiar chat; the answer stays short, and the
full evidence (alarm summary, tool trace with raw requests and responses, sources, discovered tools) is one click
away in a drawer.

## 14. One Python project, separate packages

The simulator, connector, MCP server, RAG and backend are separate packages with their own folders, settings
and tests, sharing one `pyproject.toml` and one lockfile (uv). One Docker image runs the three Python services
with different commands; the GUI has its own nginx image.

## 15. Security defaults

Secrets only in `.env`, held as `SecretStr` so they never print. Bearer tokens compared in constant time on both
the MCP server and the API. DNS-rebinding protection on the MCP endpoint. Parameterised SQL only. Container ports
bound to localhost, a non-root user with read-only code, and security headers (including a content security
policy) on the GUI. No write operations exist; any future one (such as creating a ticket) must require explicit
confirmation.

## 16. Tests use real parts

Most tests connect the real components in memory (ASGI transports, an in-memory MCP client, the real embedding
model) instead of mocks, so they test behaviour rather than wiring. Mocks are used only where a failure has to be
forced (timeouts, 429s, a broken model). The end-to-end test starts real servers over HTTP, and CI starts the
Docker stack and asks the acceptance question.

## 17. The code guarantees the facts, the model does the thinking

Code builds every factual section of an answer from the evidence: what the data shows, the likely causes, the
procedure with its key step, and the actions, with the procedure's step first. The model writes what needs
reasoning: a 2 to 3 sentence summary that answers the question, an analysis (the most likely cause and the
first action, and why), its view of the Alarm API's advice, and caveats. Its text is checked: every citation key
must exist, and every number and code it mentions must appear in the evidence.

Real answers showed why. When the model wrote every section, one answer lost its procedure section (the model
cited "S1 step 6", which the validator rejected), one misstated a number ("1 alarm" where the fact said 5), one
called alarms "failures", and one skipped most of the data. Copying numbers into sentences and deciding whether
to show the procedure are not reasoning; weighing the evidence is.

*Cost:* the facts part reads plainer than a model would write it, and the analysis exists only with a model.
*Instead of:* more guards around a model-written answer, which is reactive (each new mistake needs a new guard),
or letting the model plan its own tool calls, which is the least predictable and uses more requests.
[Decision 19](#19-one-agent-with-the-playbooks-as-recipes) later added a bounded agent, but the principle holds:
code still builds the facts, and the model's text is still checked.

## 18. A healthy chat context

The model sees the chat so far with each question, but not all of it: the last 10 turns in full (the question and
the answer's summary and analysis) and, for older turns, only their important bits (question, asset, alarm, time
window). Citation keys are removed, because `[T2]` in an old answer would point at the wrong fact in a new one.
The context is information, not instructions, and the model is told to take every number from the new question's
tools; the summary check enforces it by rejecting numbers that only the old chat holds.

*Cost:* a few hundred to a few thousand extra tokens per question, and follow-ups depend on the model (the rules
use only the last asset, alarm, site and unit). *Instead of:* sending the whole chat, which grows without limit
and invites reusing stale alarm data, or sending nothing, which made "and over the last 30 days?" impossible.

## 19. One agent with the playbooks as recipes

With a model, one agent answers every question. At each step the model picks one action: a recipe, one of the 13
read-only MCP tools, a document search, or "done" with the answer. A recipe is one of the 6 playbooks that read
live data (investigate an asset, active alarms, highest priority, related assets, procedure lookup, recommendation
check), so a common question is one step. An unusual one combines small tools: "compare pump 101 and 102" asks for
each pump's alarm summary. The limits are in code: at most 6 steps, one recipe per question, read-only tools,
arguments checked against each tool's schema, and a repeated call is not run again. The model names the answer
type (count, list, compare, why, what to do, document, cannot answer) and writes a direct answer, which leads the
answer. Code still builds the details and runs the checks of decisions 7 and 17. Without a model the keyword
rules choose a playbook, as before.

*Why:* a question outside the 8 playbooks fell through to a document search ("how many refineries do we have?",
"which motor alarmed most?"), and every new kind of question needed new code. The playbooks stay as recipes
because they are tested and chain the right tools in the right order, safety search included, so the model does
not have to rediscover that for each question.

*Cost:* about 2 model calls for a question a recipe covers (choose the recipe, then answer; the chat title is one
more for a new chat), and 3 to 7 for the rest, so the worst case is bounded by the step limit. What the answer
says now depends on the model's choices of steps, which no unit test can fix in advance; the evaluation measures
it (4 questions that only the agent can answer, run with the model). Answers built from small tools have plainer
details than a recipe's.

*Instead of:* a router in front of an agent, which makes two paths and a router that can choose wrong, and the
wrong choice is silent. Or plan-then-execute, where the model writes the whole plan first and so cannot use one
step's result in the next ("the busiest motor" must be found before its alarms can be read).
