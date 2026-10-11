# Test coverage

Measured on 11 October 2026 with `pytest-cov` over the Python packages (statement coverage).

| Component | Statements | Covered | Coverage |
|---|---:|---:|---:|
| Alarm API simulator | 1046 | 1027 | 98% |
| Alarm API connector | 209 | 202 | 97% |
| MCP server | 566 | 510 | 90% |
| RAG | 941 | 891 | 95% |
| Copilot backend | 1616 | 1492 | 92% |
| **Total** | **4378** | **4122** | **94%** |

383 Python tests and 66 GUI tests pass. The GUI tests (vitest and Testing Library) cover the screens, saved chats,
address handling and the live-stream parser; GUI line coverage is not measured.

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
| Orchestration | `apps/backend/tests/test_investigation.py`, `test_answer.py`, `test_understanding.py` | Multi-step chains passing outputs between tools, RAG in the same workflow, combined answers, partial failures, conflicting evidence |
| Backend API | `apps/backend/tests/test_api.py`, `test_stream.py`, `test_conversation_api.py` | HTTP endpoints, live stream, saved chats, validation, degraded modes |
| Language model | `apps/backend/tests/test_llm.py`, `test_openrouter.py`, `test_conversation_reply.py`, `test_titles.py` | Providers against a fake HTTP layer, retries, guardrails, titles |
| End to end | `tests/e2e/test_e2e_mcp_rag.py` | Three real servers over HTTP and the acceptance question through the backend, plus a wrong-token degraded run |
| Stack smoke test | `scripts/smoke_stack.py` (CI Docker job) | The running Docker stack, asked the acceptance question through the GUI's `/api` route |
