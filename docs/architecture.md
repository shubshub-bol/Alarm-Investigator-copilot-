# Architecture

The Alarm Investigation Copilot answers an operator's question about plant alarms with evidence from two
sources in one workflow: live alarm data through our own **MCP server**, and the plant's procedures through
**document RAG**. Every statement in the answer cites a tool fact `[T#]` or a document passage `[S#]`, and the
GUI shows the full tool trace.

![Architecture diagram](architecture-diagram.png)

The diagram source is [architecture-diagram.svg](architecture-diagram.svg). The numbers match the request flow
below.

## Components

| Concern | Where | What it does |
|---|---|---|
| User interface | `apps/frontend` (React 19, Vite, TypeScript) | Chat with saved chats, live progress, answer with citation chips, evidence drawer (alarm summary, MCP trace with raw request and response, sources, tool discovery) |
| Copilot orchestration | `apps/backend/copilot` (FastAPI) | `service.py` runs one question end to end: small-talk check, the agent (a playbook chosen by rules when there is no model), grounded answer, title, save |
| Agent | `agent.py` | One loop of model-chosen steps over the 6 recipes, the 13 MCP tools and the document search; at most 6 steps, read-only |
| Question reading | `understanding.py` | Keyword rules recognise small talk, choose the playbook when there is no model and fill what the agent leaves out of a recipe's inputs; a typed schema checks those inputs; a named asset always gets its live data |
| Planning (playbooks) | `investigation.py` | 8 playbooks, one per intent, chaining MCP calls and document searches; 6 of them are the agent's recipes |
| MCP client / tool registry | `mcp_client.py` | Discovers tools and their schemas, validates arguments and results, isolates failures |
| MCP server | `mcp-servers/alarm-management/alarm_mcp` | 13 typed, read-only tools over the Alarm Management API; auth, trace ids, retries, error codes |
| Source-system connector | `connectors/alarm_api_client` | Reusable async client: bearer auth, timeouts, retries with backoff, paging, typed errors |
| Source system | `services/alarm-simulator` | The Alarm Management API, built from the assignment's Postman collections, with synthetic data |
| RAG ingestion | `rag/ingestion` | Load, screen, sanitise, chunk, embed, save the index |
| Retrieval service | `rag/retrieval` | Hybrid search, filters, confidence, citations, untrusted-context rendering |
| Evidence and answer | `evidence.py`, `facts.py`, `consistency.py`, `answer.py`, `text_check.py` | The evidence record, filled by one recorder from every tool result; citable tool facts, including lookups that found nothing; the facts part built by code; the model's checked direct answer and analysis (no evidence, no claim); API advice vs documents |
| Answer quality | `evaluate.py` | 34 golden operator questions through the whole copilot, judged on what the operator gets (asset, live data, sources, key facts, first step, procedure, valid citations; the intent only on the rules path), plus 4 that only the agent can answer, run with a model (answer type and the numbers in the direct answer) |
| Domain models | pydantic models in each package (`alarm_mcp/schemas.py`, `copilot/trace.py`, `rag/models.py`, ...) | Typed contracts at every boundary |
| Language model | `llm.py` | One `LlmProvider` protocol; Gemini, OpenRouter or offline |
| Chat context | `context.py` | What a chat remembers for the model: the last 10 turns in full, older turns as their important bits |
| Persistence | `conversations.py` | Saved chats in SQLite |
| Configuration | `config.py` per package, `.env` | pydantic-settings, prefixed variables, secrets as `SecretStr` |
| Observability | every service | Structured JSON logs and one trace id per question; the trace is also shown in the GUI |

Each part can run and be tested on its own: the simulator and the MCP server are independent programs, the
connector is a library, RAG has its own commands, and the backend reaches alarm data only through MCP.

## Request flow: from prompt to grounded answer

The walk-through uses the assignment's acceptance question: *"Investigate recurring high-severity alarms for
Boiler Feed Pump 101 over the last 90 days, identify likely contributing factors, retrieve the relevant
operating procedure, and provide recommended actions with source evidence."*

**① Ask.** The GUI posts the question to `POST /api/chat/stream` with the open chat's id (none for a new chat)
and keeps the response open. The backend answers with Server-Sent Events: `started`, `understanding`,
`discovery`, one `step` per tool call or document search, and finally `answer` (or `error`). The GUI shows the
progress live. A new trace id is created for the question.

**Chat context.** `conversations.resume` loads the saved chat's state: the last asset, alarm, site and unit, so a
follow-up such as "Which procedure applies to this alarm?" knows what "this alarm" is. A new chat gets an id but
is only saved once its answer is ready.

**② Understand.** `understanding.understand_with_rules` reads the question with keyword rules: intent plus asset,
site, unit, time window, severities and alarm topic. Small talk ("hello") is recognised here and answered with a
short reply inside guardrails, without tools or documents. With a model, the agent chooses the steps (a recipe,
tools, document searches) and writes the direct answer; without one, the rules choose a playbook.

**The agent.** `agent.py` answers one question by taking one model-chosen step at a time, at most 6. At each step
the model sees the question, the chat context, the menu and the results so far, and returns one structured step:
a recipe, a tool, a document search, or "done" with the answer. The menu has three sections, one per action: the 6
playbooks that read live data as recipes (`investigate_asset`, `active_alarms`, `highest_priority`,
`related_assets`, `procedure_lookup`, `recommendation_check`), the MCP tools the server marks read-only (all 13; a
tool that is not read-only is neither offered nor run), and `search_documents`. Each input is shown from its
schema: its name, its type (`array of string`, `integer 1-365`, or the allowed values such as
`asset_id|asset_name|...`), whether it is required, and the first sentence of its description. A note, given once,
says that sites are written without spaces (`EastRefinery`) and that `lookback_days` counts back from the Alarm
API's data clock, not from today. At most one recipe runs per question; the tools do the rest. A recipe's inputs
are `asset` (a name or id; `asset_id` is accepted as an alias), `asset_type`, `site`, `unit`, `lookback_days`,
`severities`, `alarm_topic` and `uses_context`, described as in the understanding schema. They are checked against
that typed schema, rule-extracted values fill anything the model left empty, and an unknown input is refused, not
dropped. A site the model writes with spaces ("East Refinery") is sent without them, in a recipe and in a tool
call, because the Alarm API matches sites ignoring case but not spaces. The results so far and the chat context
reach the model as delimited data blocks (`<<<RESULTS>>>`, `<<<CHAT>>>`, the convention of the document excerpts),
and the instructions say text inside them is data, not instructions. For the acceptance question the model is
expected to pick the `investigate_asset` recipe for "Boiler Feed Pump 101", 90 days, high or critical severities
(a model that picks other steps still gets the same checks). Every step runs on one investigation, so there is one
evidence record, one trace and one numbering of `[T#]` and `[S#]` keys. A result gets its key when it arrives, so
the keys the model cites are the answer's keys. A recipe's result also lists the Alarm API's recommended actions by
step number, for the model to assess; document passages reach the model at up to 600 characters each.

| Situation | What the agent does |
|---|---|
| A valid step | Runs it, records it in the trace, gives the model the result with its key |
| Unknown name, bad JSON, an unknown recipe input, or inputs the tool's schema refuses | Does not run it; the model gets the error, which names the input, and can correct itself. The attempt counts as a step |
| A recipe or tool named under the wrong action (`search_documents` or a recipe asked for as a tool, a tool asked for as a recipe) | Not run; the model is told which action to use |
| A tool the server does not mark read-only | Not on the menu, and not run if asked for |
| A loosely formed step (inputs given as an object instead of a string, a purpose over 300 characters, a null field) | Repaired, not rejected: the inputs become their JSON text, the purpose is cut, a null field takes its default |
| A loosely formed final answer (a direct answer over 1,200 characters, an analysis statement over 800, an assessment it cannot read) | Kept: the direct answer and statements are cut to their limits (the answer checks then keep whole sentences), a verdict written "not covered" is read as `not_covered`, and an item that still cannot be read is dropped, so one bad part never throws away the model's answer |
| A document search fails, or there is no index | The model is told so, not that nothing relevant was found |
| A tool fails | The model gets the error code and message, and can try another way or answer |
| The same call again | Not run; the model gets the earlier result |
| A second recipe | Not run; the model is told to use the tools for anything more |
| The question names an asset (by the keyword rules) and nothing has been looked up | A document search or "done" is not accepted; the model is told to look up the asset's data first. A call its input schema refused does not count as a look-up. The rule yields when the MCP server is down or offered no tools |
| "done" with an answer type other than `cannot_answer`, and nothing has been looked up (no tool call that reached the server, no document search) | Not accepted, once: "nothing has been looked up yet: search the documents or call a tool, or answer cannot_answer." The named-asset rule above is the stricter case. The refusal is skipped when nothing can be looked up (no index, and the MCP server down or without tools); the answer checks then decide |
| A tool succeeds and its main list is empty (assets, alarms, alarm groups, trend points, alarm pairs, floods or candidates) | The model is told what was found in words, with the count, before the shortened data (`found 0 alarms: {...}`), and the result becomes a negative fact with its own key. An empty list whose total is above 0 (a page past the end) is not "found 0": it is stated as returned, with the total in the data |
| A document search runs and finds nothing relevant | The model gets a negative fact with a key it can cite ("The document search for '...' found 0 relevant passages."; with a document type filter, "... in safety instruction documents found 0 ..."), not a bare "nothing" |
| A long result | The model sees whole items only, up to 10 (fewer if needed to stay under 3000 characters), then `... N more (M in all)`; raw `meta` blocks are dropped |
| A search past the 8-passage evidence limit | The model is told how many passages were found but not kept. The agent's own searches do not use up a recipe's limit: before a recipe runs, the limit is raised by the passages they kept, so the recipe still gets its procedure, safety and governance passages. A passage the agent's search kept and the recipe finds again takes the recipe's purpose, so it still gives the answer its procedure or safety step |
| The last step | The model is told it must be "done" with the final answer |
| 6 steps used without an answer | The loop stops; the answer is built from what was found, with the gap "The question could not be fully answered within 6 steps." and status `partial` |
| The model fails or sends an invalid reply | The same, with a gap that gives the model's error and status `degraded`. If it fails before its first step, the question is answered by the rules instead, marked `degraded` |

**③ MCP: discover and chain tools.** The MCP client opens a session with the MCP server (streamable HTTP,
bearer token) and lists the 13 tools with their input and output schemas; the GUI's Tools tab shows them. The
`investigate_asset` playbook (typically the agent's first step for this question, or the rules' choice without a
model) then calls 8 tools, each using the previous results, all with the same trace id:

| # | Tool | Uses | Finds |
|---|---|---|---|
| 1 | `search_assets` | the asset name from the question | `AST-BFP-101` |
| 2 | `get_asset_metadata` | the asset id | details and related assets, such as drive motor M-101 |
| 3 | `summarize_alarms` | asset id, 90 days, severities | the most frequent alarm: `PMP-VIB-HI` (high vibration) |
| 4 | `get_alarm_trends` | asset id + that alarm code | whether it is getting more frequent |
| 5 | `correlate_alarms` | the asset + its related assets | what usually comes first: `MTR-BRG-TEMP-HI` on M-101 |
| 6 | `get_alarms` | asset id + alarm code, newest first | the latest occurrence |
| 7 | `score_alarm_priority` | that alarm's id | score 0-100 and band P1-P4, with the reasons |
| 8 | `get_operator_recommendations` | that alarm's id | the Alarm API's likely causes and recommended actions |

Inside the MCP server each tool validates its input, calls the Alarm API through the connector (bearer token,
`x-trace-id`, timeout, retries), validates the response against its output schema and returns it with
`meta.api_calls`. A failed tool call is recorded as a failed step and the playbook continues with what it has.

**The evidence record.** Every tool call, whether a playbook or the agent made it, passes through one door,
`Investigation.call_tool`. After a successful call it hands the result to `record_result` (`evidence.py`), which
writes what that result plainly shows into the evidence record. Playbooks no longer copy results into the record;
they keep only their judgements: the top alarm, the focus alarm, the ranking, which contributing factors are strong
enough, and the notes. So an agent that calls tools directly fills the same record: the evidence panel shows its
asset and alarms, and a follow-up such as "this boiler" has an asset to refer to.

| Tool | What the recorder writes |
|---|---|
| `search_assets` | Every asset found, as a known asset (`known_assets`, by asset id). A search never chooses the question's asset on its own, but it can make known an asset that a single-asset call then focuses on |
| `get_asset_metadata` | The asset (the fields the asset fact uses) and its related assets; the call is about that asset |
| `get_alarm_details` | The alarm (the first one stays) and its asset as known; the call is about that asset |
| `get_alarms` | With `status=active`: the active alarms. Filtered to one asset id: the call is about that asset |
| `summarize_alarms` | For exactly one asset id and not grouped per asset: the alarm summary, and the call is about that asset |
| `get_alarm_trends` | For exactly one asset id: the trend, and the call is about that asset |
| `score_alarm_priority` | The priority (the first one stays) |
| `get_operator_recommendations` | The API's advice and the trace step that produced it (`recommendations_step`), its priority, and the asset it names as known; the call is about that asset |
| `correlate_alarms`, floods, rationalization candidates, KPI tools | Nothing beyond their fact. The playbook judges which pairs are contributing factors |

Two rules keep the record honest. **One asset:** the question's asset is set only while every call about a single
asset is about the same one, and that asset is known (found by a search, or in the details of an alarm or asset).
Calls about two different assets leave it open, a summary grouped per asset is a comparison and sets nothing, and
a search with several results never sets it. **One alarm's advice:** the API's advice is "the advice" only while it
was fetched for one alarm. Advice for a second, different alarm leaves it open; the facts that state it stay under
"What the data shows", and nothing is shown as the advice for either. When the advice is held, the causes and the
recommended actions cite the fact from its own step (`recommendations_step`), whoever made the call. A playbook that
the agent runs as a recipe starts from a clean record for its own subject (`begin_playbook`): the asset and its
related assets, the alarm, the priority, the advice, the summary, the trend and the active alarms are forgotten,
while what was found about the plant stays (the known assets, the passages, the findings that nothing was found, the
notes and the gaps).

**Nothing found is a fact, not a gap.** A lookup that works and finds nothing is evidence ("There are 0 active
alarms on boiler assets"), so it becomes a fact with its own key `[T#]`, tied to the trace step that established it.
Only a lookup that succeeded can establish it: a failed call is a failed step with a note, never "found nothing".

| Where | What becomes a fact |
|---|---|
| A playbook | Each negative finding it writes after a lookup (`_found_nothing`), tied to the latest successful step of that tool among the playbook's own steps: "There are 0 active alarms on boiler assets (Boiler B-201).", "No asset matches 'Boiler Feed Pump 999'.", "There are 0 high/critical alarms on X in the last 90 days.", "No alarm on X or its related assets consistently preceded the main alarm (no clear contributing factor)." |
| The agent's tool calls | A successful result whose main list is empty (see the situation table above), worded with the count |
| A document search | A search that runs and finds nothing: "The documents have no procedure for boiler alarms." in a playbook, "No relevant passages were found in the document corpus for this question." for a main search without a more precise wording, "The document search for '...' found 0 relevant passages." for the agent. A search that could not run (no index, an error) stays a gap |

Findings about alarms state the count as the number 0, not "no", so a model's "0 active alarms [T2]" passes the
number check. Every such fact is marked `negative`. In a playbook's facts the negative ones follow the positive ones,
in the order the lookups ran; the agent's tool facts keep the order of its calls. A plain summary does not count a
negative fact as "something looked up": it cites one when there is nothing else to say (or, for a procedure question,
when the documents have no procedure). For a run of the agent without a recipe, whose model text was rejected, the
plain summary names every tool that ran, the ones that found nothing too, and states each negative fact in its words
without the data behind them ("get_alarms(...) found 0 alarms [T2]").

A negative fact states exactly the scope its lookup checked, so a model that cites it cannot widen it: the asset's
name when the query was filtered to one asset (else the site and unit, else "the plant"), the severities and the
alarm topic the question asked for, and for a search by asset type the assets it covered, named
("0 critical 'vibration' alarms on boiler assets (Boiler B-201)"). A lookup that was cut short establishes nothing:
when the one query for every active alarm stops at its cap of 200 and none of the type's assets is among them, the
answer gets a note ("The active-alarm list was capped at 200; the boiler assets may have alarms beyond it.") and no
"0 active alarms". A playbook that says why it has nothing to show ("Name an asset, site or unit ...", "No alarm in
context ...") counts only the findings of its own steps, so an earlier finding of the agent does not hide the reason.

**Gaps** are now only what could not be looked up or failed: the MCP server down, a tool error, no document index, a
search error, a missing input such as "Name an asset, site or unit to list its active alarms.", and the step limit.
One warning joins them: "The passages found for this question are weak matches." for a main search whose passages
are low confidence. Notes (assumptions and selections) are listed with the gaps.

**④ RAG: search the documents.** The playbook builds its searches from the MCP results: the procedure for
`PMP-VIB-HI` on a pump (finds `SOP-BFP-001 §4.1`), guidance on the contributing factor "motor bearing
temperature before pump vibration" (maintenance, troubleshooting and knowledge documents only), and isolation
rules before an inspection (finds `SAF-005 §2`). Each search is hybrid (BM25 + vectors), filtered and scored; each
passage gets a citation key `[S1]`, `[S2]`, ... numbered once for the whole question.

**Conflict check.** `consistency.py` compares every API recommendation with the passages. Here the API advises
"Continue operation and monitor vibration trend for 24 hours", but `SOP-BFP-001 §4.1` says not to keep a feed
pump in service above the vibration limit: a **conflict**. The playbook then searches for the rule on which
guidance governs and finds `ALM-PHIL-006 §7` ("the approved procedure governs").

**⑤ Grounded answer.** The code guarantees the facts and the model does the thinking. `facts.py` turns the tool
results into short numbered facts `[T1]`, `[T2]`, ... each tied to its trace step (a small tool's result becomes
one fact: the tool, its inputs and its shortened result), and code builds every factual section from the
evidence: what the data shows (every fact, including the ones that say a lookup found nothing, each cited), the
likely causes, the procedure (each item quoting its key step), and the actions (the procedure's step first, then the
API actions that match the documents, then the safety step). When the agent
ran a recipe, the plain summary, the causes and the actions cite the recipe's own facts, even if the agent called
the same tools earlier about another asset or alarm; every fact is still listed under the data. Without a recipe,
the tool facts are the findings and any retrieved passages are the procedure section. The model has seen every
result as the steps ran, so its "done" step carries its own part and no further model call is made: the answer type
(`count`, `list`, `compare`, `why`, `what_to_do`, `document` or `cannot_answer`), a direct answer of 1 to 3
sentences in that shape (a number first for "how many", side by side for "compare", the first step for "what should
I do"), an analysis (the most likely cause and the first action, and why), an assessment of each API action the
recipe listed (by step number: consistent, conflict or not covered, with the passages it rests on), and gaps. Its
instructions keep the rules of the answer step it replaces: never recommend an API action that conflicts with a
document, an alarm is not a failure or a trip unless a fact says so, and a question that is not about the plant's
alarms, assets or documents is answered `cannot_answer` with what the copilot can help with. Its text is checked
(`text_check.py`): every citation key must exist, and every number and code it
mentions must appear in the evidence; a direct answer that is too long is cut to its first whole sentences, and one
that cites nothing gets the keys its numbers and codes point to. **No evidence, no claim:** when there are no facts
and no passages at all, any direct answer or analysis statement is rejected with the reason `no evidence was
looked up`, unless the answer type is `cannot_answer` (an honest "I cannot answer that" needs no key). A
`cannot_answer` with no evidence at all shows only its direct answer: the model's analysis and gaps are dropped, as
they could only come from its memory. The instructions tell the model that a lookup that found 0 is a result to state
with its key, and that gaps are what could not be looked up or failed, not what a lookup found none of. A direct
answer that fails is replaced by the plain summary built from the evidence; an analysis statement that fails is
dropped. A conflict found by the
rule-based check is kept even if the model missed it, and no direct answer, action or analysis statement may
recommend an API action the documents conflict with. The answer's `answer_type` is set only when the model's
direct answer is shown, and `grounding.mode` is `llm` only when the agent produced a final reply. When there is no
final reply (step limit, model failure), the answer is the recipe's plain summary, or without a recipe a plain
summary of what was looked up. Without a model, the answer has the same facts part and the plain summary, and no
answer type.

**⑥ Name and save.** For a new chat, a short title is generated while the tools run (rules make one if the
model cannot). The answer is added to the chat's context: the question and the answer's summary and analysis,
without citation keys; beyond 10 turns, older ones keep only their important bits (question, asset, alarm, time
window). The next question's model calls read this context as information, not instructions, so follow-ups such
as "and over the last 30 days?" can be understood, while every number still comes from that question's tools. The
question, the full response and the updated context are saved in one SQLite transaction.

**⑦ Show.** The `answer` event reaches the GUI: priority badge, the direct answer with citation chips, a "Check
before acting" warning for the conflict, and links to the evidence. "What to do" stays on the answer when the
answer type is `what_to_do` or `why`, when a conflict warning is shown, and for answers without a type (the rules
path); for the other types the steps move to the evidence panel with the rest of the details. The trace lists every
tool call; each agent step shows its stated purpose. The chat appears in the sidebar and the address becomes
`/c/{id}`, so a reload reopens it.

## Where MCP and RAG meet

They are not two demos: MCP results decide what to search (asset type, alarm name and code, the leading alarm,
each recommended action), and the documents decide whether the API's advice can be followed. The answer keeps
the three kinds of evidence apart: measured facts from tools `[T#]`, documented requirements `[S#]`, and the
Alarm API's recommendations, each with a verdict of consistent, conflict or not covered.

## Authentication and trust boundaries

| Boundary | Mechanism |
|---|---|
| Browser to backend | Same origin through nginx (Docker) or the Vite proxy (dev); the browser holds no secrets. No user login: this is a single-user local tool (see known limitations) |
| Backend to MCP server | `Authorization: Bearer MCP_AUTH_TOKEN` on `/mcp`, compared in constant time; DNS-rebinding protection on allowed hosts. `/health` is public |
| MCP server to Alarm API | `Authorization: Bearer ALARM_API_TOKEN`, checked in constant time by the simulator |
| Backend to language model | Provider API key over HTTPS; optional (offline mode needs none) |
| Documents to language model | Treated as untrusted: injection screening and quarantine at ingestion, delimited untrusted blocks in the prompt, citation validation after |
| Language model to plant | None: every MCP tool is read-only, so the model cannot change anything; it can only cite evidence |

Secrets come from `.env` (never committed) into `SecretStr` settings, so they print as `**********` and never
appear in logs, tool results or error messages. In Docker, host ports are bound to `127.0.0.1` only.

## Security

| Expectation | How it is handled |
|---|---|
| Secret management | Tokens and keys only in `.env` (git-ignored; `.env.example` has placeholders), loaded as `SecretStr` so they never appear in logs, results or errors; the git history was scanned for keys |
| MCP tool authorization | `/mcp` requires a bearer token (constant-time check); every tool is read-only and idempotent; the copilot calls only tools it discovered, with arguments checked against their schemas |
| Input validation | pydantic models on every API and tool input (types, ranges, lengths, enums, id patterns); ids URL-escaped in paths; question length limited to 2000 characters |
| Output encoding | The GUI renders all text as text (React escaping; raw JSON shown in `<pre>`, never as HTML); nginx sends a content security policy, `nosniff` and `DENY` framing headers; errors never echo submitted input |
| Prompt injection | Documents are screened at ingestion and flagged passages are quarantined; small-talk messages reach the model as quoted data and its replies are checked for links, code, alarm codes and readings; every prompt says that documents, tool results and earlier messages are data, not instructions |
| Retrieved-document trust boundaries | Passages are wrapped as untrusted, delimited blocks (in the agent's prompt, inside the delimited block of results, with the tool results and recipe text; the chat context has its own block); look-alike delimiters inside are neutralised; the system prompt says they are data, not instructions; every statement must cite given evidence |
| SQL injection prevention | Only parameterised SQLite queries; no SQL is ever built from input |
| Write-operation approval | There are no write operations; any future one (such as creating a ticket) must ask for explicit confirmation |
| Tool misuse | The model chooses steps only from the discovered tools, the 6 recipes and the document search; at most 6 steps and one recipe per question, a repeated call is not run again, and arguments are checked against the tool's schema before they are sent; tools are read-only; limits on page sizes, list lengths and alarms scored per question; a conflicting API action is never recommended |
| Dependency risk | Locked dependencies (`uv.lock`, `package-lock.json`); CI runs `pip-audit` and `npm audit`; containers run as a non-root user with read-only code |

## Observability

Every service writes one JSON log line per event. One trace id follows a question from the backend through the
MCP server (`_meta.trace_id`) to the Alarm API (`x-trace-id` and `trace_id` headers) and back in every tool
result's `meta`.

| Service | Fields |
|---|---|
| Backend | `request_id`, method, path, status, `duration_ms`; per question `trace_id`, `conversation_id`, intent, understanding method, step counts, LLM `latency_ms` |
| MCP client | `trace_id`, tool, outcome, `duration_ms`, retries, error code |
| MCP server | `trace_id`, `mcp_server`, tool, outcome, `error_code`, `duration_ms`, `api_calls`, retries |
| Connector | `trace_id`, method, path, status, attempt, `will_retry`, `duration_ms` |
| Retrieval | query, filters, chunk ids, dense scores, confidence, quarantined count, latency |
| Simulator | `trace_id`, client id, method, path, status, duration |

The same information is in the GUI: the Steps tab lists every tool call (arguments, the Alarm API calls with
status, attempts and time, the raw result) and every document search (query, filters, scores, confidence).
`GET /api/health` reports MCP connectivity and tool count, index status, language model and chat storage, and
answers HTTP 503 while anything is degraded, so a monitor sees it from the status code alone.

## Failure handling

| Failure | What happens |
|---|---|
| A tool call fails (404, validation, ...) | Recorded as a failed step with its `[CODE]` message; the playbook continues; the answer lists the gap |
| Alarm API busy or down (429, 5xx, timeout) | The connector retries with backoff (3 retries), then the tool returns `[RATE_LIMITED]` or `[UPSTREAM_UNAVAILABLE]` |
| MCP server unreachable or token wrong | Discovery fails; the answer is built from documents only and says so; status `degraded` |
| Tool or argument not offered by the server | Rejected by the MCP client before sending: `TOOL_NOT_FOUND`, or `INVALID_INPUT` against the discovered input schema |
| A tool call hangs | `MCP_TIMEOUT` after 60 s; recorded as a failed step |
| Language model down or invalid JSON | Before the agent's first step: the rules choose a playbook and a template writes the answer, marked `degraded`. Later: the answer is built from what the steps found. Either way the answer says the model was unavailable |
| The agent reaches 6 steps without an answer | The answer is built from what was found, with the gap "The question could not be fully answered within 6 steps."; status `partial` |
| No model, and a question the rules cannot place | The document search runs; if it finds nothing relevant, the gap is "This question needs the language model." A comparison, ranking or count that names an asset, type or site gets that playbook's answer instead, which may not answer the question asked |
| A lookup works and finds nothing (no active alarms, no matching asset, no relevant documents) | A negative fact with its own key, cited in the answer ("There are 0 active alarms on ..."). It is a finding, not a gap |
| Weak documents | Confidence `low`: the passages are used and the gap says "The passages found for this question are weak matches."; nothing irrelevant is cited |
| Document index missing | The answer uses alarm data only and says documents were unavailable |
| Chat storage unavailable | Answers still work, marked `saved: false` (the GUI keeps its address); chat endpoints return 503 with a clear message; the GUI shows "Chats not saved" |
| Saved follow-up context unreadable | The chat continues without its earlier context; a warning is logged |
| A saved answer unreadable | That answer is skipped when the chat opens; the others are shown; a warning is logged |
| Chat deleted while its answer runs | The answer is shown but not saved (`saved: false`), so the chat stays deleted |
| Backend unreachable | The GUI shows the problem and reconnects on its own; a failed question offers "Try again" |

## Deployment

`docker compose up --build` builds two images from the root `Dockerfile` and starts four services in order,
each behind a health check:

| Service | Image target | Port (localhost) | Health check | Starts after |
|---|---|---|---|---|
| `alarm-api` | `python-app` | 8000 | `GET /health` | |
| `alarm-mcp` | `python-app` | 9000 | `GET /health` | `alarm-api` healthy |
| `copilot-backend` | `python-app` | 8080 | `GET /api/live` | `alarm-mcp` healthy |
| `frontend` | `gui` (nginx) | 3000 | `GET /` | `copilot-backend` healthy |

The Python image installs from `uv.lock`, downloads the embedding model and builds the document index at build
time, runs as a non-root user, and keeps code read-only; saved chats live in the `chats` volume. nginx serves the
GUI, forwards `/api` (with buffering off for the live stream) and sends `/c/{id}` to the app. It looks the backend's
address up through Docker's DNS at request time, so a restarted backend container is still reached. The backend's
health check is liveness only: a slow or stopped MCP server degrades answers (to documents only) but does not mark
the backend unhealthy, because restarting it would not help. Without a `.env`
file the stack runs in offline mode with demo tokens.

## Persistence

Saved chats use SQLite (`COPILOT_DB_PATH`, default `.data/copilot.sqlite3`): a `conversations` table (id, title,
how the title was set, timestamps, follow-up context as JSON) and a `turns` table (question, full response as
JSON, order). Writes happen in one transaction per answered question, foreign keys are enforced, WAL mode lets
the list be read while an answer is saved, and every query is parameterised.

## Repository layout

```text
apps/backend/copilot          copilot backend (orchestration, MCP client, answer, chats)
apps/frontend                 React GUI
mcp-servers/alarm-management  the MCP server
connectors/alarm_api_client   reusable Alarm API connector
services/alarm-simulator      Alarm Management API simulator and its seed data
rag/                          ingestion, retrieval, documents, evaluation, tests
tests/                        unit (connector), integration (connector + simulator), e2e (all services)
test-data/postman             the assignment's Postman collections
scripts/                      smoke tests and documentation generators
deploy/nginx.conf             GUI web server configuration
docs/                         this documentation
```

The simulator lives in `services/`, which the suggested layout does not have: it is the source system, not part
of the copilot, so it is kept apart from `apps/`.
