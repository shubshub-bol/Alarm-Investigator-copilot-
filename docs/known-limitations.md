# Known limitations and future improvements

## Limitations

**Scope and data**

- The Alarm Management API is a simulator with synthetic data from 1 April to 1 July 2026. Its clock stops at the
  end of the data, and no new alarms arrive while it runs.
- The nine documents are synthetic and short. PDF text is extracted without OCR or layout analysis, so scanned
  PDFs would need OCR first.
- English only.

**Copilot**

- Eight question types (intents) have playbooks. Other questions fall back to a document search. Flood analysis,
  rationalization and KPI tools exist on the MCP server but no playbook uses them yet.
- Without a language model, intent detection uses keyword rules and the answer uses templates. Unusual phrasing
  can get the wrong intent, and the wording is plainer. The rules do not read the chat's earlier messages, only
  its last asset, alarm, site and unit, so a follow-up such as "and over the last 30 days?" needs the model.
- The chat context sent to the model holds the last 10 turns (each answer cut to 800 characters) and 20 lines about
  older turns. A question that needs more of an old answer than that, or that compares two assets, is not
  supported.
- Free OpenRouter models allow 20 requests a minute and 50 a day (1000 a day after buying credits). A question
  uses about three model calls and takes 10 to 25 seconds with a model, under a second without one.
- The Alarm API's recommendations are checked against the documents with a rule-based check and the model. A
  conflict phrased very differently from the procedure could be missed by the rule-based part.
- The model's text is checked for valid citations and for numbers and codes found in the evidence, not for
  meaning: a real number put in the wrong place, or a weak argument, can pass. The risk is small because code
  writes every factual section, the model writes only the summary and the analysis, and every statement shows
  its sources so the operator can check it.
- The answer-quality set (`python -m copilot.evaluate`, 34 questions) checks the intent, the asset, the use of
  live data, the sources, key facts, the first step, the procedure and the citations, not the model's wording. In CI it runs offline; with the model
  it is run by hand, a few questions at a time, because of the free plan's daily limit.

**Security and operations**

- There is no user login and no per-user data. It is a single-user tool for this machine, and Docker binds its
  ports to localhost. Anyone who can reach port 3000 can use it.
- There are no write operations. If one is added, such as creating a ticket, it must ask for explicit
  confirmation first.
- Observability is structured logs plus the trace shown in the GUI. There are no metrics and no tracing backend
  (such as OpenTelemetry).
- The backend is one process with the index in memory and SQLite for chats, which suits a single user, not a
  shared deployment.
- `COPILOT_DB_PATH` defaults to a relative path (`.data/copilot.sqlite3`). Start the backend from the repository
  root, or set an absolute path. Docker sets one.

**Small items found in review and deferred**

- The chat list is ordered by the newest answer's row id. A database `VACUUM` could renumber rows and change the
  order.
- If the chat database cannot be opened when the backend starts, saved chats stay off until the backend is
  restarted, even after the problem is fixed.
- Rule-based chat titles have no length limit, unlike model titles (60 characters).
- A chat deleted while an answer for it is still being written can reappear when that answer is saved.
- Stored answers have no format version, so a future change to the answer format would need a migration.
- One corrupt stored answer makes that chat fail to open (HTTP 500) instead of skipping the bad turn.
- While the backend is unreachable, the sidebar keeps showing "Loading chats" instead of an error.
- Accessibility gaps in the chat menu: no menu roles, Escape does not close it, and the mobile overlay stays open
  when the window is widened.
- During a language model outage, a new chat still tries one title call before falling back to a rule-based
  title.
- The backend still generates follow-up question suggestions, which the GUI no longer shows.

## Future improvements

- Connect to a real alarm system: point `ALARM_API_BASE_URL` and the token at it. The connector and MCP server
  need no code change if the API follows the same contract.
- Add playbooks for floods, alarm rationalization and KPIs. Or let the model plan with tools under guardrails
  for open questions, keeping playbooks for the core flows.
- Add a second MCP server for maintenance work orders or ticketing, with write tools behind explicit
  confirmation.
- Add user sign-in (OIDC), per-user chats and role-based access to tools.
- For a larger corpus: a vector database (pgvector or Qdrant) behind the same retriever interface, a reranker,
  incremental indexing and OCR.
- Add OpenTelemetry traces and metrics across the backend, MCP server and API.
- Extend the answer-quality set with the expected facts of each answer, and run it with the model on a schedule.
- Cache tool results within a chat, so follow-up questions do not repeat calls.
