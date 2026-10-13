# Known limitations and future improvements

## Limitations

**Scope and data**

- The Alarm Management API is a simulator with synthetic data from 1 April to 1 July 2026. Its clock stops at the
  end of the data, and no new alarms arrive while it runs.
- The nine documents are synthetic and short. PDF text is extracted without OCR or layout analysis, so scanned
  PDFs would need OCR first.
- English only.

**Copilot**

- Eight question types (intents) have playbooks. With a model, 6 of them are the agent's recipes, and the agent
  combines the 13 MCP tools for other questions, including flood analysis, rationalization and KPIs, which no
  playbook covers yet. Without a model, a question no playbook covers falls back to a document search.
- The agent takes at most 6 steps and runs one recipe per question. A question that needs more is answered from
  what was found, with the gap "The question could not be fully answered within 6 steps." and the status
  `partial`. A comparison of two assets is done with the small tools, so its details are plainer than a recipe's.
- Questions only the agent can answer need a model. Without one, a question the rules cannot place gets the
  document search and, if nothing relevant is found, the gap "This question needs the language model." ("How many
  refineries do we have?" is one). A comparison, ranking or count that names an asset, an asset type or a site gets
  that playbook's answer instead, which may not answer the question asked ("Compare Boiler Feed Pump 101 and 102"
  gets the investigation of pump 101 only).
- Without a language model, intent detection uses keyword rules and the answer uses templates. Unusual phrasing
  can get the wrong intent, and the wording is plainer. The rules do not read the chat's earlier messages, only
  its last asset, alarm, site and unit, so a follow-up such as "and over the last 30 days?" needs the model.
- The chat context sent to the model holds the last 10 turns (each answer cut to 800 characters) and 20 lines about
  older turns. A question that needs more of an old answer than that is not supported.
- Free OpenRouter models allow 20 requests a minute and 50 a day (1000 a day after buying credits). A question one
  recipe covers uses about 2 model calls (one step to choose it, one to answer); other questions use 2 to 6; a new
  chat's title is 1 more. Each step is a model call, so a question with several steps is slower; without a model
  it takes under a second.
- The Alarm API's recommendations are checked against the documents with a rule-based check and the model. A
  conflict phrased very differently from the procedure could be missed by the rule-based part.
- The model's text is checked for valid citations and for numbers and codes found in the evidence, not for
  meaning: a real number put in the wrong place, or a weak argument, can pass. The risk is small because code
  writes every factual section, the model writes only the direct answer and the analysis, and every statement
  shows its sources so the operator can check it.
- With the agent, the model also chooses the steps, so an answer can rest on the wrong tool or on an incomplete
  list (a long result is shown to the model cut to 10 items, with the total). The checks do not see that. The
  answer depends on the model's choices, which is why the evaluation has questions only the agent can answer.
- The answer-quality set (`python -m copilot.evaluate`) has 34 questions that run offline and 4 that only the agent
  can answer, which are skipped offline. It checks the intent, the asset, the use of live data, the sources, key
  facts, the first step, the procedure and the citations, not the model's wording. The 4 model-only questions
  check the answer type and the whole numbers in the direct answer, and pass only on a model-written direct
  answer. In CI the 34 run offline; with the model the set is run by hand, a few questions at a time, because of
  the free plan's daily limit. The free Gemini tier's daily limit bounds how often the model-only questions can
  be evaluated.

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
- Add recipes for floods, alarm rationalization and KPIs. The agent can already use their tools, but without a
  tested order of steps.
- Add a second MCP server for maintenance work orders or ticketing, with write tools behind explicit
  confirmation.
- Add user sign-in (OIDC), per-user chats and role-based access to tools.
- For a larger corpus: a vector database (pgvector or Qdrant) behind the same retriever interface, a reranker,
  incremental indexing and OCR.
- Add OpenTelemetry traces and metrics across the backend, MCP server and API.
- Extend the answer-quality set with the expected facts of each answer, and run it with the model on a schedule.
- Cache tool results within a chat, so follow-up questions do not repeat calls.
