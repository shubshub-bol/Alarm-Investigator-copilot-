# Test coverage

Measured on 13 October 2026 with `pytest-cov` over the Python packages (statement coverage).

| Component | Statements | Covered | Coverage |
|---|---:|---:|---:|
| Alarm API simulator | 1049 | 1031 | 98% |
| Alarm API connector | 209 | 202 | 97% |
| MCP server | 566 | 510 | 90% |
| RAG | 944 | 894 | 95% |
| Copilot backend | 2491 | 2362 | 95% |
| **Total** | **5259** | **4999** | **95%** |

572 Python tests and 82 GUI tests pass. The GUI tests (vitest and Testing Library) cover the screens, saved chats,
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
| Orchestration | `apps/backend/tests/test_investigation.py`, `test_answer.py`, `test_understanding.py`, `test_context.py` | Multi-step chains passing outputs between tools, RAG in the same workflow, combined answers, partial failures, conflicting evidence |
| Agent | `apps/backend/tests/test_agent.py` | The agent loop with a fake model that plays scripted steps, so no test spends a real request: a recipe in one step, small tools and document searches, bad names and inputs, a failing tool, a repeated call, a second recipe, a long result, the named-asset rule, the step limit, model errors, the direct answer and its checks, the rules path without a model; the menu's input types, values and shared-input note, a step under the wrong action, loosely formed steps repaired, a site written with spaces, read-only tools only, delimited results and chat, the last step, a failed search, citations and passages kept for a recipe that follows the agent's own calls, and the model's action assessments |
| Answer quality | `apps/backend/tests/test_evaluate.py`, `test_text_check.py` | 34 golden operator questions through the whole copilot (offline): intent, asset, live data, sources, key facts, first step, procedure, valid citations; the 4 model-only questions (answer type, whole numbers in the direct answer, a model-written answer), which are skipped offline; the agent's intent accepted on a question without live data; each question's agent steps in the report; the check on the model's numbers and codes |
| Backend API | `apps/backend/tests/test_api.py`, `test_stream.py`, `test_conversation_api.py` | HTTP endpoints, live stream, saved chats, validation, degraded modes |
| Language model | `apps/backend/tests/test_llm.py`, `test_openrouter.py`, `test_conversation_reply.py`, `test_titles.py` | Providers against a fake HTTP layer, retries, guardrails, titles |
| End to end | `tests/e2e/test_e2e_mcp_rag.py` | Three real servers over HTTP and the acceptance question through the backend, plus a wrong-token degraded run |
| Stack smoke test | `scripts/smoke_stack.py` (CI Docker job) | The running Docker stack, asked the acceptance question through the GUI's `/api` route |
