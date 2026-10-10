# Chat sessions: design

Date: 2026-10-10. Status: agreed in review, not yet built.

## Goal

The GUI holds one conversation in memory: a page reload or a backend restart loses it. Operators should be able
to keep several chats, come back to them later and continue them, in a familiar ChatGPT-like layout.

Success: create two or three chats, switch between them, reload the page or restart the backend, and every chat,
its answers and its follow-up context are still there.

## Decisions

| Topic | Decision |
|---|---|
| Users | One user, no login. |
| Where chats live | The copilot backend, in SQLite through Python's built-in `sqlite3` (no ORM, no new dependency). |
| Chat list actions | New chat, open, rename, delete, search by title. No date groups. |
| Names | Generated once, after the first real question: a short LLM title, or a rule-based one offline. Never the truncated question. |
| Look | ChatGPT-like layout (sidebar, centred empty state, question bubbles). Our existing colours and fonts stay. |

## Backend

### Storage

One SQLite file, `COPILOT_DB_PATH` (default `.data/copilot.sqlite3`, git-ignored; a volume in Docker). Created with
its tables on startup if missing. `PRAGMA user_version = 1` records the schema version; WAL journal mode lets the
list be read while an answer is being saved; `foreign_keys = ON` makes deletes cascade.

```sql
CREATE TABLE conversations (
    id           TEXT PRIMARY KEY,                -- conv-<12 hex>, issued by the server
    title        TEXT NOT NULL,
    title_source TEXT NOT NULL CHECK (title_source IN ('placeholder', 'auto', 'user')),
    created_at   TEXT NOT NULL,                   -- ISO 8601, UTC
    updated_at   TEXT NOT NULL,
    state        TEXT NOT NULL                    -- ConversationState as JSON (follow-up context)
);
CREATE TABLE turns (
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    seq             INTEGER NOT NULL,             -- 1, 2, 3... within the conversation
    question        TEXT NOT NULL,
    response        TEXT NOT NULL,                -- the full ChatResponse as JSON: answer, evidence, trace
    created_at      TEXT NOT NULL,
    PRIMARY KEY (conversation_id, seq)
);
CREATE INDEX conversations_by_update ON conversations (updated_at DESC);
```

Each call opens its own short-lived connection, so the store is safe to use from FastAPI's thread pool. The async
chat path calls it through `asyncio.to_thread`; the list, open, rename and delete endpoints are plain `def`
endpoints, which FastAPI runs in its thread pool.

The in-memory store and its 200-conversation limit are removed: saved chats are only deleted by the user.

### Conversation lifecycle

1. A question arrives with a conversation id or none. A known id loads that chat's follow-up context; no id or an
   unknown id gets a new server-issued id (clients never choose ids). The `started` event carries the id, as today.
2. The investigation runs and may update the follow-up context.
3. When the answer is ready, one transaction inserts the chat row (if new), appends the turn with the full
   response, stores the updated context, sets `updated_at` and, if due, the title. Then the `answer` event is sent.
4. A question that fails or is cancelled (the browser left) is not saved, so the list never shows empty chats.

Two tabs writing to the same chat at once each get the next `seq` inside the transaction; the follow-up context is
last-writer-wins.

### Names

New module `copilot/titles.py`.

- A chat whose turns so far are only small talk is titled "New chat" (`placeholder`).
- The first real question (any intent except `conversation`) names it (`auto`), once.
- A rename sets `user`; the copilot never changes a `user` or `auto` title afterwards.

For a chat that still needs a title and an LLM is configured, the title is one `generate_text` call started right
after query understanding, so it runs in parallel
with the investigation and normally adds no wait. If it has not finished 3 s after the answer is ready, or fails, or
its output is unusable, the rule-based title is used. Prompt: "Reply with only a title of 2 to 6 words in Title Case
that names the asset, site or topic. No quotes, no trailing punctuation." The reply is cleaned: first line only,
quotes and trailing punctuation stripped, whitespace collapsed; accepted if it has 2 to 8 words and at most 60
characters.

Rule-based title (offline, or LLM fallback): `"{subject}: {topic}"`, or `topic` alone when there is no subject.

- Subject: the focus alarm's asset name, else the resolved asset's name, else site and unit, else site, else unit.
- Topic by intent:

| Intent | Topic |
|---|---|
| `investigate_asset` | the focus or most frequent alarm's name (e.g. "High Vibration"), else "Alarm Investigation" |
| `active_alarms` | "Active Alarms" |
| `highest_priority` | "Highest Priority Alarm" |
| `related_assets` | "Related Assets" |
| `procedure_lookup` | "{alarm name} Procedure", else "Operating Procedure" |
| `recommendation_check` | "Advice Check" |
| `knowledge_question` | the alarm topic in Title Case, else the title of the first cited document |

Examples: "Boiler Feed Pump 101: High Vibration", "EastRefinery: Highest Priority Alarm", "Alarm Management
Philosophy". Titles are plain text and shown as text, never as HTML.

### API

| Method and path | Purpose | Success |
|---|---|---|
| `GET /api/conversations` | the list, newest activity first | 200 `{"conversations": [{"conversation_id", "title", "created_at", "updated_at"}]}` |
| `GET /api/conversations/{id}` | one chat with all its turns | 200 summary fields + `"turns": [{"question", "created_at", "response": ChatResponse}]` |
| `PATCH /api/conversations/{id}` | rename; body `{"title": "..."}` | 200 the updated summary |
| `DELETE /api/conversations/{id}` | delete the chat and its turns | 204 |

- Ids must match `^conv-[a-f0-9]{12}$` (422 otherwise); a well-formed unknown id is 404.
- Titles are trimmed and must be 1 to 80 characters (422 otherwise).
- CORS additionally allows `PATCH` and `DELETE`.
- `GET /api/health` gains `"storage": {"ok": true, "conversations": 12}` (or `"ok": false, "error": "..."`), and
  reports `degraded` when storage is not ok.
- `POST /api/chat` and `/api/chat/stream` keep their request and response shapes.

### Errors

- The database cannot be opened at startup: the backend still starts, health reports storage not ok, chat answers
  still work, and the list endpoints answer 503 with a clear message.
- Saving a turn fails (e.g. disk full): the user still gets the answer; the error is logged with the trace and
  conversation ids, and health reports storage not ok. If it was a new chat's first answer, that chat does not
  exist afterwards, so reopening its address shows "This chat doesn't exist".

### Configuration

- New: `COPILOT_DB_PATH` in settings and `.env.example`.
- Removed: `COPILOT_MAX_CONVERSATIONS`.
- `.gitignore`: `.data/`.

## Frontend

### Layout

```
┌──────────────────────┬──────────────────────────────────────────┐
│ Alarm Copilot    [◧] │                                 ● Ready  │
│ ✎ New chat           │                                          │
│ ⌕ Search chats       │      What would you like to investigate? │
│                      │  ╭────────────────────────────────────╮  │
│ BFP-102 Critical Al… │  │ Ask about an alarm, asset or site ⬆│  │
│ EastRefinery: High…  │  ╰────────────────────────────────────╯  │
│ New chat             │    Investigate recurring alarms on …     │
│                      │    Show active critical alarms for …     │
└──────────────────────┴──────────────────────────────────────────┘
```

- Sidebar: app name and collapse button, New chat, search box, chat list (newest first, open chat highlighted).
  Collapsed, it is a thin strip of icons (expand, new chat, search), remembered in `localStorage`. Under 768 px it
  is hidden and opens over the page from a menu button.
- Empty chat: the greeting, the input box and the starter questions centred on the page.
- Chat with turns: each question is a bubble on the right; each answer is plain text on the page (no card); the
  input box sits at the bottom. The progress line, evidence drawer, citation chips and follow-up suggestions stay.
- Send button: a round button with an up arrow, accessible name "Ask".
- Colours and fonts are the existing tokens: greys, with colour only for alarm priorities, conflicts and faults.

### Behaviour

- The address is `/` for a new chat and `/c/{id}` for a saved one. A reload reopens the chat; Back and Forward move
  between chats. A new chat's address changes to `/c/{id}` when its first answer arrives (replace, not push).
- Opening a chat loads its turns from `GET /api/conversations/{id}` and shows them as finished answers.
- After every answer the list is reloaded, so a new chat and its title appear at once.
- Search filters the loaded list by title, ignoring case; "No chats match" when nothing does.
- Rename: "⋯" menu, then the title becomes a text field. Enter or leaving the field saves; Esc or an empty title
  cancels. Renaming does not move the chat in the list (`updated_at` changes only when a turn is added).
- Delete: "⋯" menu, then a dialog "Delete chat? This deletes "{title}" and its answers." with Cancel and Delete.
  Deleting the open chat goes to a new chat.
- Opening another chat or a new chat while an answer is running stops that answer; it is not saved.

### Code structure

- `api/client.ts`, `api/types.ts`: `listConversations`, `getConversation`, `renameConversation`,
  `deleteConversation` and their types.
- `state/useRoute.ts`: reads and changes the address (`pushState`, `replaceState`, `popstate`); no router library.
- `state/useConversations.ts`: the list with loading and error state, `refresh`, `rename`, `remove`.
- `state/useChat.ts`: gains the open conversation id and `load(id)`, with loading and not-found states.
- `components/Sidebar.tsx` (frame, new chat, search, collapse), `components/ChatList.tsx` (items, menu, rename),
  `components/ConfirmDialog.tsx` (native `<dialog>`), `components/icons.tsx` (inline SVG icons, no icon library).
- `TopBar.tsx` becomes the main area's header: menu button on small screens and the status.
- `Conversation.tsx`: centred empty state, question bubbles, "This chat doesn't exist" state.

### Errors

- The list fails to load: "Couldn't load chats" and a Retry button in the sidebar.
- `/c/{id}` for a deleted or unknown chat: "This chat doesn't exist" and a New chat button.
- Rename or delete fails: a short message next to the chat; nothing changes.
- Storage not ok in health: the status reads "Chats not saved".

## Testing

Backend (pytest):

- Store, on a temporary file: a chat is listed only after its first answer; turn order; the full response round
  trip; follow-up context round trip; placeholder title replaced by the first real title; `auto` and `user` titles
  never overwritten; delete cascades to turns; a second store on the same file sees everything (restart).
- Names: each rule above; LLM reply cleaning; LLM failure, timeout and unusable output fall back to rules; small
  talk keeps "New chat".
- API: the four endpoints with 200, 204, 404 and 422 cases; a chat question creates a titled conversation; after
  rebuilding the app on the same file, the list, the turns and a follow-up question's context survive; health shows
  storage; CORS allows `PATCH` and `DELETE`.

Frontend (vitest, Testing Library):

- Sidebar and chat list: rendering, search and "No chats match", rename with Enter and Esc, delete with confirm and
  cancel, the open chat highlighted.
- App: the address becomes `/c/{id}` after the first answer and the list reloads; loading `/c/{id}` shows that
  chat's answers; an unknown id shows "This chat doesn't exist"; New chat goes back to `/`.

## Out of scope

Login and multiple users, searching inside answers, sharing, pinning, archiving, date groups, renaming by the LLM
after the first title, finishing an answer after the user leaves its chat, retention or export.

## Follow-ups in later steps

- Docker: a named volume for `.data/`, and an nginx fallback to `index.html` so `/c/{id}` reloads work.
- Docs: chat history in the README, the new endpoints in the API docs, single-user SQLite in known limitations.
