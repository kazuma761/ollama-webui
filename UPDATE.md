# UPDATE: branch `14-db-connector` (22 October 2026)

Built on `new-db-approach13` (workspaces, SQL connector, big files, agent skills).
This branch adds a new sidebar and Settings, moves the Admin panel behind the admin
password, corrects the docs (the server runs llama.cpp, not Ollama), and records a full
test of the database connector, workspaces and the UI.

Where things run:
- **Server:** the app, with `llama-server` serving Gemma 4 12B (port 8080) and Llama 3.1 8B
  (port 8081).
- **Development Mac:** no local model. Tested there by AI test agents, Docker test databases
  and stand-in model servers. Anything that needs a real model reply is listed under
  "Test on the server".

---

## 1. New in this branch

### Sidebar (like Open WebUI), on the chat page
`frontend/index.html`
- **Top:** Fastshot, a button to hide the sidebar, **New chat**, **Search chats**, and links
  to **Workspaces**, **Documents** and **Databases**. These replace the old top menu.
- **Workspace:** a section that folds open to pick the workspace every answer is based on, or
  to add one. It replaces the floating "Workspaces" panel.
- **Saved chats:**
  - grouped by day (Today, Yesterday, Previous 7 days, Previous 30 days, Older);
  - click one to reopen it with its charts, sources and notes, and carry on;
  - each has a delete button;
  - search looks at titles and what was said.
- **Account button at the bottom:** opens a menu with Settings. Admin panel and Sign out of
  admin appear there only after an admin signs in.
- **Wide screens:** the sidebar stays open and the chat moves right; the composer's design is
  unchanged.
- **Phones and tablets:** it slides over the chat and starts closed; Escape closes it.

### Saved chats (in the browser)
- Each chat is saved in the browser (`localStorage`, key `fastshot.chats`) after every reply,
  up to 200 chats. When the browser's storage is full, the oldest chats are dropped.
- **Pictures attached to a message are not saved**: they would fill the browser's storage.
- The extra saved fields (`view`, `names`) stay in the browser and are not sent to the server.
- Chats are per browser, not per person. Server-side history needs logins first.

### Settings window, with the Admin panel for the admin only
- **General tab:**
  - "Delete all chats";
  - the admin password box (the server's `ADMIN_PASSWORD`). Sign-in lasts until the tab is
    closed.
- **Admin panel tab:** appears only after a correct admin password. It is the existing
  `admin.html`, shown inside the window without its own header (`admin.html?embed`).
- **Sign-out** removes the tab and the panel.
- A password stored from earlier is checked again when the page loads; if it has changed on
  the server, the user is signed out.
- The "Admin" link was removed from every page's menu: documents, databases, workspaces, word,
  invoices.
- Hiding the panel only tidies the screen. The server still checks the password on every admin
  request.

### Docs now say llama.cpp, not Ollama
- **`AGENTS.md`:**
  - new section **"Where it runs (read first)"**: the server, the two llama-servers, the
    config to use (`ollama/config.llamacpp/` copied to `ollama/config.local/`), and the
    development Mac with no model;
  - "Start here", "Setup", "Models: one file" and "Fit the models" now give the
    llama-server steps instead of `ollama pull`.
- **`README.md`, `PROJECT_FLOW.txt`:** first lines and Run section corrected.
- **`DETAIL.md`, `AGENTS.md`:** chat history is now "in the browser only", not "none".

---

## 2. Fixed

- **Navigation came back as a sidebar.** It had been a top menu plus a floating panel.
- **The Admin panel was linked from every page for everyone.** Now it is only in Settings,
  after the admin password.
- **The docs told the next agent to install Ollama and `ollama pull` models.** They now
  describe the llama-server setup.
- **Admin panel inside Settings:** its cards sat inside a second dark box. Removed.

---

## 3. Tested (on the development Mac, without a real model)

### Self-checks: all pass
`python -m ollama_pipeline.selfcheck`, `python -m sql_connector`, `python -m workspace_engine`,
`python -m agent_skills`.

### Agent team test (21 October)
Three Claude Code agents in tmux: db-tester, workspace-tester and ui-tester. They were given
the skills ponytail, unlazy, vibe-security, code-review and ponytail-review.

| Area | Passed | Failed | Needs a model |
|---|---|---|---|
| Databases (PostgreSQL, MySQL, SQLite in Docker) | 16 | 0 | 1 |
| Workspaces, memories, skill switches | 53 | 0 | 4 |
| UI: sidebar, Settings, Admin, phone and desktop | 30 | 2 | 2 |

Highlights:
- 57 of 57 unsafe queries refused on all 3 databases.
- Row counts unchanged after the run.
- Kannada and Hindi data round-trips correctly.
- No console errors and no sideways scrolling at 375 px or 1440 px.

### Database chat, end to end (22 October)
Docker PostgreSQL and MySQL plus a SQLite file, read-only user. The model was a stand-in
llama-server that streams tool calls the way Llama 3.1 does.

**All three databases:**
- The model listed the tables, read the columns, sent a query with a wrong column, got the
  database's error back, corrected it, and answered.
- The totals match a direct query on PostgreSQL.

**On the chat page:**
- picking the database in the model menu shows the "Database: shop-pg" pill;
- the header reads "Database · Llama 3.1 8B";
- four lookup blocks appear, then the answer.

**Refusals and errors:**
- "delete the cancelled orders": refused, and all 200 orders are still there;
- no database picked: the model cannot reach one (expected);
- llama-server without `--jinja`: a clear error telling you to start it with `--jinja`;
- "Database queries" switched off: 403.

### 100+ page PDF question (22 October)
A 120-page PDF (about 118,000 tokens) against an 8,192-token slot:

| Question | Result |
|---|---|
| A question about a fact on page 87 | ✅ only page 87 sent (432 tokens); Sources shows "[1] page 87" |
| The same question reworded | ✅ page 87 still found |
| A follow-up, "and how often?" | ❌ see bug B1 |

---

## 4. Needs fixing (found, not fixed in this branch)

### Bugs

| # | Severity | What | Where |
|---|---|---|---|
| S1 | **High** | A saved database password can be sent to any host: "Test" with an empty password reuses the saved one even after the host changed. Proven with a fake server. | `backend/app/sql/routes.py:40-44`, `sql/sql_connector/store.py:68-69` |
| S2 | **High** | The SQL API has no login and accepts any SQLite path. Anyone who can reach the app can open other files on the server as databases. | `backend/app/sql/routes.py:70-88`, `sql/sql_connector/connectors.py:334-337` |
| B1 | **High** | A short follow-up question about an attached file ("and how often?") is searched on its own, so the file's passage is lost. Workspaces already search it together with the question before it. One-line fix: search attached files with `state.workspaces.question(messages)`. | `backend/app/routes.py:259` |
| B2 | Medium | Database answers end in a Markdown table, but the chat prints it as raw `\|` lines: its Markdown display only handles code and bold. | `render()` in `frontend/index.html` |
| S3 | Medium | The one-second wait after a wrong admin password doesn't slow down guesses sent at the same time: 30 took 1.3 s. | `backend/app/admin/routes.py:20-29` |
| S4 | Medium | The query check can be bypassed with PostgreSQL `E'\''` strings. The read-only transaction still stopped every write. | `sql/sql_connector/safety.py:47-52` |
| B3 | Medium | Creating the same workspace name at the same time makes duplicates. | `workspaces/workspace_engine/index.py:316-324` |
| B4 | Medium | "Forget" deletes a memory without asking when two match equally. | `skills/agent_skills/memory.py:144-149` |
| B5 | Medium | An upload is received in full before the 50 MB check. | `backend/app/workspaces/routes.py:127` |
| B6 | Low | When nothing in a file matches the question, Sources lists 8 unrelated passages from pages 1-3. | `workspaces/workspace_engine/chatfiles.py:230-233` |
| B7 | Low | The "no model can call tools" error still says `ollama pull qwen3:14b`. It should say to start Llama 3.1 8B on llama-server with `--jinja` (and `tools: true`). | `ollama/ollama_pipeline/pipeline.py:140` |
| B8 | Low | Keyboard focus is lost after Settings closes. | `frontend/index.html` (`settings` close) |
| B9 | Low | Connection names in Kannada or Hindi get broken ids. | `sql/sql_connector/store.py:25-28` |

The full list of 21 findings, with how each was proven, is in the agent team's report. It was
written on the development Mac (`out/team-test/REPORT.md`) and is not in git.

### Fix first
1. **S1:** keep a saved password only when engine, host, port and user are unchanged.
2. **S2:** put the admin password on the SQL write, test and delete routes, and allow SQLite
   files from one folder only.
3. **B1:** the one-line follow-up fix.
4. **B2:** tables in the chat's Markdown display.

---

## 5. Set up and test on the server

### Setup
- `git fetch && git checkout 14-db-connector`
- Packages: `cd backend && uv sync --extra router --inexact` (or
  `pip install -r requirements.txt`). Nothing new compared with `new-db-approach13`.
- Start both llama-servers **with `--jinja`**, and Gemma also with `--mmproj`. The commands
  are at the top of `ollama/config.llamacpp/models.yaml`.
- `ollama/config.local/models.yaml` must have the `llama` entry with `tools: true` and
  `sql: model: llama`. Copy them from `ollama/config.llamacpp/models.yaml` if this server's
  copy is an old Ollama one. Otherwise database chats say "None of the local models here can
  call tools".
- Set `ADMIN_PASSWORD` (a long one) in `backend/.env`. Without it the Admin panel stays closed.
- Restart, then refresh the browser with Ctrl+Shift+R.

### Check without a model
- `cd backend && python -m ollama_pipeline` lists both hosts as `ok (llama.cpp …)` and
  `llama … ready, calls tools`.
- `python -m sql_connector`, `python -m workspace_engine` and `python -m agent_skills` all
  pass.

### Check with the real models

**Database chat:**
1. Databases page: save a database, Test, Save.
2. Chat: click the model name, pick it under "Answer from a database", check the pill.
3. Ask a question. The header must be "Database · Llama 3.1 8B", with lookup blocks above the
   answer.
4. Check that Llama 3.1 writes SQL for the right dialect.
5. Ask it to delete something: it must be refused.

**Gemma picked by hand with a database:** it is used if the app cannot tell it can't call
tools. Check whether it really calls them; if not, use Auto or Llama.

**100+ page PDF:**
- a question about one fact: the right page under Sources, and Gemma cites [1];
- "summarise this PDF": "Reading … parts read" counts up;
- a follow-up question: fails until B1 is fixed.

**50,000-row Excel:** "total sales by region as a chart" gives SQLite SQL, the right totals
and a chart.

**`/props`:** the context per request matches `-c` / `-np`, with and without `--kv-unified`.

**Sidebar and Settings:**
- saved chats reopen with their answers;
- the Admin tab appears only after the password;
- on a phone the sidebar slides over the chat.

**Memory and Sources:** watch `nvidia-smi` and the `llama_kv_cache … size` line while both
models run.
