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
| Copilot orchestration | `apps/backend/copilot` (FastAPI) | `service.py` runs one question end to end: understanding, playbook, grounded answer, title, save |
| Intent detection | `understanding.py` | Intent and entities from the language model as JSON, keyword rules as fallback |
| Planning (playbooks) | `investigation.py` | 8 playbooks, one per intent, chaining MCP calls and document searches |
| MCP client / tool registry | `mcp_client.py` | Discovers tools and their schemas, validates arguments and results, isolates failures |
| MCP server | `mcp-servers/alarm-management/alarm_mcp` | 13 typed, read-only tools over the Alarm Management API; auth, trace ids, retries, error codes |
| Source-system connector | `connectors/alarm_api_client` | Reusable async client: bearer auth, timeouts, retries with backoff, paging, typed errors |
| Source system | `services/alarm-simulator` | The Alarm Management API, built from the assignment's Postman collections, with synthetic data |
| RAG ingestion | `rag/ingestion` | Load, screen, sanitise, chunk, embed, save the index |
| Retrieval service | `rag/retrieval` | Hybrid search, filters, confidence, citations, untrusted-context rendering |
| Evidence and answer | `facts.py`, `consistency.py`, `answer.py` | Citable tool facts, API advice vs documents, LLM or template answer, citation validation |
| Domain models | pydantic models in each package (`alarm_mcp/schemas.py`, `copilot/trace.py`, `rag/models.py`, ...) | Typed contracts at every boundary |
| Language model | `llm.py` | One `LlmProvider` protocol; Gemini, OpenRouter or offline |
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

**② Understand.** `understanding.understand` asks the language model for JSON that matches a typed schema: intent
plus asset, site, unit, time window, severities and alarm topic. Here that is `investigate_asset`, "Boiler Feed
Pump 101", 90 days, high or critical. Precise rule-extracted values fill anything the model left empty, and the
keyword rules take over completely if the model fails or is switched off. Small talk ("hello") is recognised by
the rules and answered with a short reply inside guardrails, without tools or documents.

**③ MCP: discover and chain tools.** The MCP client opens a session with the MCP server (streamable HTTP,
bearer token) and lists the 13 tools with their input and output schemas; the GUI's Tools tab shows them. The
`investigate_asset` playbook then calls 8 tools, each using the previous results, all with the same trace id:

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

**④ RAG: search the documents.** The playbook builds its searches from the MCP results: the procedure for
`PMP-VIB-HI` on a pump (finds `SOP-BFP-001 §4.1`), guidance on the contributing factor "motor bearing
temperature before pump vibration" (maintenance, troubleshooting and knowledge documents only), and isolation
rules before an inspection (finds `SAF-005 §2`). Each search is hybrid (BM25 + vectors), filtered and scored; each
passage gets a citation key `[S1]`, `[S2]`, ... numbered once for the whole question.

**Conflict check.** `consistency.py` compares every API recommendation with the passages. Here the API advises
"Continue operation and monitor vibration trend for 24 hours", but `SOP-BFP-001 §4.1` says not to keep a feed
pump in service above the vibration limit: a **conflict**. The playbook then searches for the rule on which
guidance governs and finds `ALM-PHIL-006 §7` ("the approved procedure governs").

**⑤ Grounded answer.** `facts.py` turns the tool results into short numbered facts `[T1]`, `[T2]`, ... each tied
to its trace step. The language model receives the facts, the API's actions to assess, the known gaps and the
passages (wrapped as untrusted text), and returns JSON: summary, findings, likely causes, procedure, recommended
actions, an assessment of each API action, and gaps. The answer is then validated: unknown citation keys are
removed, findings or actions left without a valid citation are dropped, a conflict found by the rule-based
check is kept even if the model missed it, and an Alarm API action that conflicts with a procedure is removed from
the recommended actions even if the model listed it. If the model is unavailable, a template builds the same
structure from the same evidence.

**⑥ Name and save.** For a new chat, a short title is generated while the tools run (rules make one if the
model cannot). The question, the full response and the updated context are saved in one SQLite transaction.

**⑦ Show.** The `answer` event reaches the GUI: priority badge, summary with citation chips, what to do, a "Check
before acting" warning for the conflict, and links to the evidence. The chat appears in the sidebar and the
address becomes `/c/{id}`, so a reload reopens it.

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
`GET /api/health` reports MCP connectivity and tool count, index status, language model and chat storage.

## Failure handling

| Failure | What happens |
|---|---|
| A tool call fails (404, validation, ...) | Recorded as a failed step with its `[CODE]` message; the playbook continues; the answer lists the gap |
| Alarm API busy or down (429, 5xx, timeout) | The connector retries with backoff (3 retries), then the tool returns `[RATE_LIMITED]` or `[UPSTREAM_UNAVAILABLE]` |
| MCP server unreachable or token wrong | Discovery fails; the answer is built from documents only and says so; status `degraded` |
| Tool or argument not offered by the server | Rejected by the MCP client before sending: `TOOL_NOT_FOUND`, or `INVALID_INPUT` against the discovered input schema |
| A tool call hangs | `MCP_TIMEOUT` after 60 s; recorded as a failed step |
| Language model down or invalid JSON | Rules for understanding, template for the answer; the answer says the model was unavailable |
| No relevant documents | Confidence `none` or `low`; nothing irrelevant is cited; the answer says what is missing |
| Document index missing | The answer uses alarm data only and says documents were unavailable |
| Chat storage unavailable | Answers still work; chat endpoints return 503 with a clear message; the GUI shows "Chats not saved" |
| Backend unreachable | The GUI shows the problem and reconnects on its own; a failed question offers "Try again" |

## Deployment

`docker compose up --build` builds two images from the root `Dockerfile` and starts four services in order,
each behind a health check:

| Service | Image target | Port (localhost) | Health check | Starts after |
|---|---|---|---|---|
| `alarm-api` | `python-app` | 8000 | `GET /health` | |
| `alarm-mcp` | `python-app` | 9000 | `GET /health` | `alarm-api` healthy |
| `copilot-backend` | `python-app` | 8080 | `GET /api/health` | `alarm-mcp` healthy |
| `frontend` | `gui` (nginx) | 3000 | `GET /` | `copilot-backend` healthy |

The Python image installs from `uv.lock`, downloads the embedding model and builds the document index at build
time, runs as a non-root user, and keeps code read-only; saved chats live in the `chats` volume. nginx serves the
GUI, forwards `/api` (with buffering off for the live stream) and sends `/c/{id}` to the app. Without a `.env`
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
