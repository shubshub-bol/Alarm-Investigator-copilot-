# Test coverage

Measured on 13 October 2026 with `pytest-cov` over the Python packages (statement coverage).

| Component | Statements | Covered | Coverage |
|---|---:|---:|---:|
| Alarm API simulator | 1049 | 1031 | 98% |
| Alarm API connector | 209 | 202 | 97% |
| MCP server | 566 | 510 | 90% |
| RAG | 944 | 894 | 95% |
| Copilot backend | 2608 | 2487 | 95% |
| **Total** | **5376** | **5124** | **95%** |

652 Python tests and 82 GUI tests pass. The GUI tests (vitest and Testing Library) cover the screens, saved chats,
address handling, the live-stream parser and which answer type shows "What to do"; GUI line coverage is not
measured.

## Reproduce

```bash
make coverage
```

Without make:

```bash
uv run pytest --cov=alarm_simulator --cov=alarm_api_client --cov=alarm_mcp --cov=rag --cov=copilot --cov-report=term --cov-report=html
```

The HTML report is written to `htmlcov/index.html`. CI runs the same command on every push and keeps the HTML
report, `coverage.xml` and the JUnit test report as the `python-coverage-and-tests` build artifact.

## What the tests cover

| Area | Test files | Kind |
|---|---|---|
| Simulator | `services/alarm-simulator/tests/` | Every endpoint: auth, trace ids, errors, filters, pagination, analytics, KPIs |
| Connector | `tests/unit/test_alarm_api_client.py` | Unit: payloads, headers, retries, `Retry-After`, typed errors, paging, path escaping |
| Connector + simulator | `tests/integration/test_client_against_simulator.py` | Integration: the real client against the real simulator |
| MCP server | `mcp-servers/alarm-management/tests/` | Tool registration and discovery, schemas, auth on `/mcp`, trace propagation, error mapping, KPI two-step calls |
| MCP client | `apps/backend/tests/test_mcp_client.py` | Connectivity, discovery, schema-checked invocation, invalid arguments, missing tools, unavailable server |
| RAG | `rag/tests/` | Extraction, chunking, metadata, injection screening, golden-set retrieval quality, filters, confidence, citations |
| Orchestration | `apps/backend/tests/test_investigation.py`, `test_answer.py`, `test_understanding.py`, `test_context.py` | Multi-step chains passing outputs between tools, RAG in the same workflow, combined answers, partial failures, conflicting evidence; negative findings tied to the lookup that found nothing, cited in the answer, kept apart from a failed lookup, worded with the scope the lookup checked (the asset, the severity, the assets covered) and not established by a capped alarm list; weak passages as a warning, not a finding |
| Evidence record | `apps/backend/tests/test_evidence.py` | One test per recorder rule (what each tool records), the one-asset rule (a search sets none, a comparison leaves it open, a summary grouped per asset is a comparison), the one-alarm advice rule, correlations recording nothing, a clean start for a playbook, and a saved chat from before still loading |
| Agent | `apps/backend/tests/test_agent.py` | The agent loop with a fake model that plays scripted steps, so no test spends a real request: a recipe in one step, small tools and document searches, bad names and inputs, a failing tool, a repeated call, a second recipe, a long result, the named-asset rule, the step limit, model errors, the direct answer and its checks, the rules path without a model; the menu's input types, values and shared-input note, a step under the wrong action, loosely formed steps repaired, a site written with spaces, read-only tools only, delimited results and chat, the last step, a failed search, citations and passages kept for a recipe that follows the agent's own calls, and the model's action assessments; the agent's own lookups filling the evidence record, an empty tool result stated with its count, an empty document search and a recipe's negative finding as cited facts, the one-time "nothing has been looked up yet" refusal, a claim with no evidence rejected unless it is `cannot_answer`, a `cannot_answer` with nothing behind it showing only its direct answer, a rejected answer still naming every lookup and stating the one that found nothing, a page past the end not read as found nothing, and a recipe still saying why it has nothing after the agent's own empty search |
| Answer quality | `apps/backend/tests/test_evaluate.py`, `test_text_check.py` | 34 golden operator questions through the whole copilot (offline): asset, live data, sources, key facts in the findings or the direct answer (whole numbers), first step, procedure, valid citations, with the intent checked on the rules path only; the 4 model-only questions (answer type, whole numbers in the direct answer, a model-written answer), which are skipped offline; the route not checked with a model; each question's tools, document searches and agent steps in the report; the check on the model's numbers and codes |
| Backend API | `apps/backend/tests/test_api.py`, `test_stream.py`, `test_conversation_api.py` | HTTP endpoints, live stream, saved chats, validation, degraded modes |
| Language model | `apps/backend/tests/test_llm.py`, `test_openrouter.py`, `test_conversation_reply.py`, `test_titles.py` | Providers against a fake HTTP layer, retries, guardrails, titles |
| End to end | `tests/e2e/test_e2e_mcp_rag.py` | Three real servers over HTTP and the acceptance question through the backend, plus a wrong-token degraded run |
| Stack smoke test | `scripts/smoke_stack.py` (CI Docker job) | The running Docker stack, asked the acceptance question through the GUI's `/api` route |
