# AGENTS.md

Instructions for an AI coding agent working on this repo on a machine it has
not seen before. Read this first, then `PROJECT_FLOW.txt` for the code map.

## What this is

A local chat web app on top of llama.cpp's `llama-server`. A user picks a model
(or "Auto"), sends a prompt with optional PDF/DOCX/text/image attachments, and
the reply streams back. With "Auto", a router sends each message to the light
or the strong local model, or, for questions about software, to OpenCode's free
cloud models. The folder and package are still called `ollama/` and
`ollama_pipeline`, and the code still talks to Ollama too, but **Ollama is not
used**.

## Latest branch: `14-db-connector`

`UPDATE.md` lists what that branch added (sidebar, saved chats, Settings with the Admin panel
for the admin only), what was tested, the bugs found and not yet fixed, and what to check on
the server. Read it before the open tasks below.

## Where it runs (read first)

- **The server runs the app.** RTX 4080 Super (16 GB), 32 GB RAM,
  i7-13700K. Two `llama-server` processes, one model each:
  - **Gemma 4 12B**, port 8080: the strong one. Hard questions, documents,
    screenshots and invoices (it reads pictures, with `--mmproj`).
  - **Llama 3.1 8B**, port 8081: the light one. Quick questions and the SQL
    connector (good at calling tools).
  - Optionally a third, small server for embeddings (nomic-embed-text), port
    8082, for searching workspaces by meaning.
  The config for this is `ollama/config.llamacpp/models.yaml`, copied to
  `ollama/config.local/` on the server. Its header has the exact
  `llama-server` commands; the section "llama-server on the RTX 4080 Super"
  below has the flags and the memory budget.
- **The development Mac has no local model.** It is an M1 with 8 GB, no
  llama.cpp and no Ollama. Code is written and tested there by AI test agents
  without a model: the self-checks, the API, the pages in a browser, Docker
  test databases, and stand-in model servers where a reply is needed. Anything
  that needs a real model reply is checked on the server; reports mark it
  "needs a model". Do not install llama.cpp or Ollama or download models
  there without asking.
- Sections further down that say `ollama pull`, `ollama ps`,
  `OLLAMA_MAX_LOADED_MODELS`, `qwen3:14b` or `llama3.2:3b` come from earlier
  test runs on Ollama. On the server, use the llama-server equivalent: the
  model files and flags in `ollama/config.llamacpp/models.yaml`.

## Open tasks: start here (asked for by the user, 19 October 2026)

Work on the branch `new-db-approach13` (or a new branch made from it). Do
these in order. When one is done, move it to "Done" at the end of this list,
say what was tested and how, and push. Keep to "Rules" near the end of this
file.

### Task 1. Check the large-file work on the real server

The branch was tested only against stand-in llama-servers (see the next
section). On the server with Gemma 4 12B and Llama 3.1 8B:

1. Run `cd backend && python -m ollama_pipeline`. Both models must show as
   ready, and the context per request must be what llama-server's `-c` /
   `-np` gives (it is read from `/props`). Try it with and without
   `--kv-unified`.
2. In the chat (Ctrl+Shift+R first):
   - Attach a 100+ page PDF and ask about one fact in it. Check that the
     Sources list shows the right page.
   - Ask "summarise this PDF". "Reading … parts read" should count up, and
     the answer should come from the notes.
   - Attach a 50,000-row Excel file and ask "total sales by region as a
     chart". The SQL must be SQLite, use the sheet's table and column names,
     and give the right figures.
3. Note the time for the summary of a 250-page file and what other users
   feel meanwhile. Reading in parts is two requests at a time per person
   (`CONCURRENT_PARTS` in `workspaces/workspace_engine/chatfiles.py`).
4. Try the llama-server flags from the next section. Read the KV cache size
   from the `llama_kv_cache ... size` line of the server log, then write the
   flags that worked into the header of
   `ollama/config.llamacpp/models.yaml`.

### Task 2. Check document generation and fix what is broken

The documents section is `frontend/documents.html`. It offers four tasks:
- **Create a new document:** `frontend/word.html`, served by
  `backend/app/documents/word.py` and `documents/document_engine/word/`.
- **Fill in a template.**
- **Change a document.**
- **Read invoices into a sheet:** `frontend/invoices.html`.

The first three are served by `backend/app/documents/routes.py` and
`documents/document_engine/engine/` and `model/`.

1. Run the checks that need no model, then go through each task in a browser
   as a person would:
   - `cd backend && python -m document_engine.word`;
   - the invoice checks described under "Invoices tab".
   Use a real model on the server, or a stand-in (an example is
   described under "Tested end to end" below). Write down every step that
   fails or confuses.
2. **Known gap.** These pages still split the model's context between their
   sources (`share()` in `documents/document_engine/word/service.py`), so a
   very big source is cut, with a note. Make them read big sources the way
   the chat now does:
   - passages that match the request, via `workspace_engine.ChatFiles` /
     `Workspace.search`;
   - or notes from reading in parts (`ChatFiles.read_whole`);
   - and spreadsheets as SQL tables (`sql_connector.import_file`) when only
     figures are needed.
   Keep "local models only" and "the model writes Markdown, never the file".
3. Fix the two old lint warnings in
   `documents/document_engine/engine/wordedit.py`: the unused `W_TBL` import
   and the unused `first`.
4. Check: `python -m document_engine.word` passes, and ruff passes
   (`uvx ruff check --select E,F,W --ignore E501 --exclude '**/.venv' .`).
   Also open a made file in Word or LibreOffice.

### Task 3. Add presentation generation (PowerPoint)

The user wants slides made like "Create a new document" makes Word files.
It is a new part of the documents section.

Build it with the same split as the Word part:
- `documents/document_engine/slides/`:
  - `prompts.py`: what the model is told. It writes the deck as Markdown, for
    example `# Slide title`, bullets, `Notes:` lines for speaker notes, a
    Markdown table, or a fenced `chart` block with labels and values.
  - `service.py`: the sources and the request go to the local model; the
    text streams back. Reuse `word/sources.py` to read the sources, and the
    big-file handling from Task 2.
  - `build.py` (no model): Markdown to slides with `python-pptx`. It covers a
    title slide, a section slide, title + bullets, two columns, a table, and
    a native chart (bar, line or pie) from the figures. A slide that
    overflows is split into two.
  - `__main__.py`: a check with no model, which builds a deck from a
    hand-written Markdown and reads it back with python-pptx.
- `backend/app/documents/slides.py`: routes like `word.py`:
  - `GET /api/documents/slides/config`
  - `POST /api/documents/slides/create`, which streams like the Word one
  - `POST /api/documents/slides/build`, which builds again from edited text
  - `GET /api/documents/slides/{job}/pdf`, through LibreOffice as in `word.py`
  A made deck is an ordinary documents job, downloaded through
  `/api/documents/{job}/download`.
- `frontend/slides.html`: the same layout as `word.html`. The page has
  sources, the request, and choices: slide count, one colour, 16:9 or 4:3,
  and an optional company template `.pptx` (its masters and layouts are used,
  its slides removed). The text is shown and can be edited, then built again
  without the model. Add a fifth task card, "Create a presentation", on
  `documents.html`.
- Dependency: `python-pptx` (MIT). Add it to `documents/pyproject.toml`, run
  `cd backend && uv sync --inexact`, then regenerate `requirements.txt` with
  the command in its header. It is not installed yet.

Rules, the same as for the Word part:
- Local models only, with no cloud path.
- The model never writes the file.
- Nothing is fetched (no pictures from URLs).
- The page's choices win over the model's text.
- Figures from Excel are copied exactly.

Check:
- `python -m document_engine.slides` passes.
- A deck from a PDF and an Excel source opens in LibreOffice Impress (and
  PowerPoint if available) with no repair prompt.
- The chart is a real chart, not a picture.
- Add a section to this file like "Word document tab": what it does, its
  rules, and what is not done.

### Done
- Big files without losing the context: the branch section below (tested
  with stand-ins; Task 1 checks it on the server).

## Running the `new-db-approach13` branch: big files without losing the context

The problem this branch fixes: with a big PDF or Excel file the model "lost
the context". Before, a file's whole text (up to 200,000 characters) went into
the chat message and the browser sent it again with every later message. One
llama-server request only holds one slot: `-c` divided by `-np` (e.g. 32768 /
4 = 8192 tokens). A 100-page PDF is about 60,000 tokens, so the request was
refused (`exceed_context_size_error`) or the model saw only part of it.

What it does now (studied from AnythingLLM, then adapted):

1. **Every request is fitted to the model's real context**
   (`ollama/ollama_pipeline/context.py`). The slot size is read from
   llama-server's `/props` (`n_ctx` per slot, re-read every 60 s;
   `ModelEntry.context`, `ModelEntry.budget`), or `num_ctx` for Ollama.
   - Room is kept for the answer (a fifth of the context, 1024 to 4096).
   - The oldest history goes first and the current turn is kept. The middle
     of a message that is still too big is cut, with a marker saying so.
   - If llama-server still refuses (`ContextOverflow`, from its own token
     count), the request is shrunk and retried, at most twice.
   - The documents and Word pages use the same real budget.
2. **Chat files stay on the server** (`workspaces/workspace_engine/chatfiles.py`,
   `POST /api/files`). They are kept in `backend/data/chatfiles/<id>/` for
   `documents.keep_hours` (24 h), and messages carry only `{id, name}`.
   For each question:
   - Files that fit are given whole.
   - From bigger ones, the passages that match the question, as many as fit
     (words with FTS5, plus meaning when the embedding model runs; vectors are
     made in the background after the upload answers). A "Sources" list shows
     them.
   - A summary request ("summarise", "key points", "whole document";
     `wants_whole`) reads the whole file in parts that fit, two at a time. It
     notes each part, combines the notes if needed, and answers from the
     notes. Notes are cached per model, so the next question does not read it
     again. The chat shows "Reading X: part i of n".
   - An expired file is named in the reply ("attach it again").
3. **Spreadsheets become SQLite tables** (`sql/sql_connector/sheets.py`).
   - The header is found under title rows, and columns are typed: Indian
     number commas, ₹, day-first dates, and leading-zero codes kept as text.
   - Up to 1,000,000 rows per sheet.
   - A small sheet (600 cells or fewer) is given whole as Markdown.
   - For a bigger one the model gets its tables, columns and 5 sample rows,
     and queries it with the read-only SQL tools (SQLite dialect). The
     database does the sums and counts, and charts work on the results.
   - AnythingLLM embeds sheets as text instead, which cannot total a column.
4. **Prompt layout for llama-server's prompt cache.** What stays the same from
   turn to turn goes in the system message: rules, the tools prompt,
   memories. What belongs to this question (passages, file text) goes in the
   last user message inside `<context>…</context>`. How the budget is shared:
   a workspace and files together get 25% and 40% of it; a workspace alone
   45%; files alone 60% (`_shares` in `backend/app/routes.py`).

What AnythingLLM does, for comparison:
- Storage: Prisma + SQLite (`workspace_chats` keeps prompt and response per
  user and thread), and LanceDB for vectors.
- Retrieval: top 4 passages, similarity 0.25, the last 20 messages of history.
- Budget: system 15%, history 15%, the user's message 70%. When that is too
  big it cuts the middle ("cannonball").
- "Pinned" documents are given whole.

We do the same, with budgets measured from the real slot instead of a fixed
model size.

Not changed: chat history still lives in the browser only. Server-side chat
storage (an SQLite table like AnythingLLM's `workspace_chats`) belongs with
logins, which do not exist yet.

### llama-server on the RTX 4080 Super (16 GB) for 10-12 people

What the GPU memory goes to:
- **Weights.** Gemma 12B Q4_K_M is about 7.3 GB, plus about 0.85 GB for
  `--mmproj`. Llama 3.1 8B Q4_K_M is about 4.9 GB.
- **The KV cache, which grows with the context.**
  - Llama 3.1 8B needs 128 KiB per token at f16, about 68 KiB at q8_0. So
    32768 tokens of context are about 2.2 GB at q8_0.
  - Gemma's sliding window makes its per-token cost smaller.
  - Read the real figure from the server's startup line
    `llama_kv_cache ... size = ...`.

**Both chat models fully on the GPU leave about 1 GB for context. That is the
main limit.** Either:
- run one chat model on the GPU and give it the context: Gemma for everything,
  with Llama as a smaller fallback partly on the CPU (`-ngl` lower); or
- keep both with small contexts.

Flags worth using:
- `-fa on -ctk q8_0 -ctv q8_0`: flash attention and a q8 KV cache. This
  halves the cache at almost no quality cost.
- `-c 32768 -np 4 --kv-unified`: 4 people answered at once. With
  `--kv-unified` the slots share the 32768 tokens, so one big request can use
  more than a quarter of them. `python -m ollama_pipeline` and
  `/props` show the per-request size the app uses.
- `--cache-reuse 256`: reuses the cached prompt start (system message,
  history) between turns.
- `--no-mmproj-offload` if pictures are rare: the vision part stays in RAM.
- The embedding server on the CPU:
  `-ngl 0 --embedding -b 2048 -ub 2048` (nomic-embed-text, about 0.3 GB).
- 10-12 people at 45-50 tokens/s in total means about 4-5 tokens/s each when
  all of them type at once. `-np` above 4 adds queueing, not speed.

Checks without a model: `cd backend && .venv/bin/python -m ollama_pipeline.selfcheck`,
`python -m sql_connector` (spreadsheets), `python -m workspace_engine`
(chat files: whole, passages, reading in parts, expiry).

**Tested end to end with stand-in llama-servers** (19 October 2026). The
stand-ins report a 4096-token slot in `/props` and refuse a bigger prompt
with `exceed_context_size_error`, as llama-server does. All of these passed:
- **The slot size was read.** `/api/models` showed `context: 4096` for both
  models.
- **A 60-page PDF** (about 76,000 tokens) was uploaded through `/api/files`.
  A question about one fact on page 37 found that passage first, sent a
  2,151-token request, and the answer quoted it.
- **"Summarise this document"** read the PDF in 30 parts, with progress
  events. No part was refused, and the largest request was 3,000 tokens. The
  answer came from the notes and kept the page-37 fact. Asking again used the
  cached notes.
- **A 20,000-row Excel file** (title rows above the header, amounts written
  as "12,345"). "Show the total amount by region as a chart" made the model
  call `query` with `SUM ... GROUP BY region`. The totals matched a Python
  sum of the file exactly, and `create_chart` drew them.
- **A server that says its slot is 8192 but refuses at 3000.** A chat with
  24 long earlier messages was refused once (5,715 tokens), shrunk, and
  answered at 1,757 tokens.
  - Fixed after that run: the "left out to fit" notice now comes with the
    request the server accepted, so it gives the real size.
  - Also fixed: the progress text now reads "Reading X: 3 of 30 parts read"
    instead of "part 0 of 30".

**Not done, check these on the server:**
- **No real model has been used**, only stand-ins. With Gemma 4 12B and
  Llama 3.1 8B, try:
  - a 100+ page PDF question (passages and Sources);
  - "summarise this PDF" (progress, then an answer from notes);
  - a 50,000-row Excel file ("total sales by region", then a chart).
  Check that Llama writes SQLite SQL for the sheet's table and column names.
- Whether llama-server's `/props` reports `n_ctx` per slot as expected with
  and without `--kv-unified`.
- Reading in parts uses the chat model for every part: a 250-page file is
  about 40 requests. Two run at once per person, so others wait behind them.
- **The browser was not clicked through for this branch.** Check:
  - the file pills ("60 pages", "1 sheet · 20,000 rows");
  - the "Reading … parts read" lookup block;
  - the notice;
  - the Sources list under a file answer.
- A file older than `documents.keep_hours` (24 h) is gone. The reply should
  name it ("attach it again"); this was not tried in a browser.
- The llama.cpp flags above are not yet in the header of
  `ollama/config.llamacpp/models.yaml`; copy them there once they are tried
  on the 4080 Super. Read the real KV size from the `llama_kv_cache` line in
  the server log.
- `documents/document_engine/engine/wordedit.py` has two old lint warnings
  (unused `W_TBL` import, unused `first`); they are not from this branch.

The next tasks the user asked for are in "Open tasks: start here" at the top of this file.

## Running the `tools` branch on the server (llama.cpp, SQL connector, workspaces)

This branch adds four things (Workspaces and Agent skills have their own sections below), and a round of fixes:

- **llama.cpp instead of Ollama.** The server runs llama.cpp's `llama-server`
  with Gemma 4 12B and Llama 3.1 8B, not Ollama. A host in `models.yaml`
  written `{url: ..., kind: llamacpp}` is a llama-server: the app talks to its
  OpenAI API (`/v1/chat/completions`) through
  `ollama/ollama_pipeline/llamacpp.py`, which gives back the same chunks as
  Ollama, so the chat, Auto, documents, invoices and the SQL tools work
  unchanged. A plain URL is still an Ollama server; both can be mixed.
  `provider: ollama` on a model still means "local" (on one of `hosts:`).
- **The SQL connector**, rebuilt from AnythingLLM's SQL agent skill
  (`server/utils/agents/aibitat/plugins/sql-agent/`). Databases are saved on
  the new **Databases** page (`frontend/databases.html`); picking one in the
  chat's model menu lets a local model answer from it with four tools:
  `list_databases`, `list_tables`, `get_table_schema`, `query`. Each lookup
  is shown in the reply (the query and the first rows).

Steps on the server:

1. **Get the branch.** `git fetch && git checkout tools`
2. **Install the new packages** (`psycopg[binary]`, `pymysql`, `numpy`, the
   project's own `sql/`, `workspaces/` and `skills/`): `pip install -r requirements.txt`, or
   `cd backend && uv sync --extra router --inexact`. SQLite needs nothing.
3. **Start both llama-servers with `--jinja`** (tool calls need it), Gemma
   with `--mmproj` (pictures). The commands are at the top of
   `ollama/config.llamacpp/models.yaml`: Gemma on port 8080, Llama on 8081.
4. **Use that config:** `cp -r ollama/config.llamacpp ollama/config.local`
   (or edit the ports and names in the copy first). If a `config.local`
   already exists, copy the `hosts:`, `sql:` and model entries into it.
5. **Check:** `cd backend && python -m ollama_pipeline` lists both hosts as
   `ok (llama.cpp ...)` and `llama ... ready, calls tools`.
   `python -m sql_connector` must print that all checks pass.
6. **Restart**, open <http://127.0.0.1:8000/databases.html>, add a database
   (Test, then Save), then in the chat open the model menu, pick it under
   "Answer from a database", and ask about its data. Ctrl+Shift+R first.

Rules to keep:

- **Read-only, three times over.** `safety.py` refuses anything but one
  SELECT / WITH / SHOW / DESCRIBE / EXPLAIN, and writes hidden inside one
  (`WITH x AS (DELETE ...)`, `SELECT ... INTO`, `FOR UPDATE`, `pg_sleep`,
  `nextval`); SQLite is opened `mode=ro`; PostgreSQL and MySQL run every
  query in a read-only transaction with a time limit (`sql.timeout`).
  AnythingLLM only asks its model to send SELECTs. Still give the app a
  database user that can only read.
- **Local models only.** A database chat never goes to OpenCode; Auto's
  routing is skipped and `sql.model` answers (or the picked model, when it is
  local and can call tools; otherwise the reply says which one answers).
- **Passwords stay on the server**, in `backend/data/sql/connections.json`
  (mode 600, not in git), as AnythingLLM keeps them. The page never gets them
  back; leaving the password empty when changing a connection keeps it.
- **The model never sees more than it asked for:** at most `sql.max_rows`
  rows per query, and at most `sql.max_rounds` lookups before it must answer.
- `ollama_pipeline` knows nothing about SQL: `chat_stream` takes any `tools`
  and a `run_tool` function; the backend passes the SQL ones in.

Not done, check these on the server:

- **No real model has called the tools.** Tested against stand-ins for Ollama
  and for llama-server that make the calls a model would (including
  llama-server's way of sending a tool call in pieces). Check that Llama 3.1
  8B calls `list_tables` before `query` and writes SQL for the right dialect;
  try `sql.model: gemma` too.
- Whether Gemma 4's template is seen as able to call tools (`/props`
  `chat_template` mentions tools). If not, add `tools: true` to its entry.
- **No live PostgreSQL or MySQL was reached**, only SQLite. Try one of each:
  Test, Tables, a question, and a write such as "delete the orders" (it must
  be refused).
- llama-server answers one request at a time unless started with `-np 2`
  or more; Auto's router and a reply then wait for each other.
- `num_ctx` cannot change llama-server's context; its `-c` does.

### Workspaces (document search with sources, RAG)

Modelled on AnythingLLM's workspaces. A team puts its documents (PDF, Word,
Excel, CSV, text; many at once) in a workspace on the **Workspaces** page
(`frontend/workspaces.html`). In the chat, the **Workspaces** panel on the
left picks one; every answer is then based on the passages of its documents
that match the question, and a "Sources" list under the answer shows each
passage (document, page) with its text.

How it works (`workspaces/workspace_engine/`, package `workspace_engine`):

1. The backend reads the file (`backend/app/workspaces/routes.py`): a PDF page
   by page, so a passage can name its page; Word, Excel, CSV and text through
   the Word tab's `read_source`. Up to 5 million characters per file.
2. `chunking.py` splits it into passages of about `chunk_chars` (1200) that
   overlap by `overlap_chars` (200) and end at a paragraph or sentence.
3. `service.py` gets a vector for each passage from the embedding model
   (`workspaces.embed_model`), and `index.py` stores passages, vectors and a
   full-text index in `backend/data/workspaces/<id>/index.db` (SQLite FTS5).
4. For a question, `index.py` ranks passages by words (BM25) and by meaning
   (vectors), combines the two (reciprocal rank fusion) and the top `top_k`
   (6) go into the system message with numbers; `prompts.py` tells the model
   to cite them as [1], [2] and to say when the documents do not cover it.
   A short follow-up ("and for flights?") is searched together with the
   question before it.

Rules to keep:
- **Local models only.** A workspace chat never goes to OpenCode: Auto picks
  a local model (`select_local_model`); picking a cloud model is refused.
- **It works without an embedding model.** Without one (not set, not running,
  or failing) passages are found by words only, and the page says so. Passages
  indexed that way get vectors later with **Re-index**. Vectors from another
  embedding model are not mixed with new ones: the workspace asks to be
  re-indexed.
- **The same file twice is skipped; a changed file with the same name
  replaces the old one.**
- To change what the model is told, edit `workspace_engine/prompts.py` only.

Embedding model: `workspaces.embed_model` in `models.yaml`. With llama.cpp, a
third llama-server started with `--embedding` (the command is at the top of
`ollama/config.llamacpp/models.yaml`, port 8082). With Ollama,
`ollama pull nomic-embed-text`. `query_prefix` / `document_prefix` are what
nomic-embed-text expects; empty them for other models.

Checks without a model: `cd backend && python -m workspace_engine`.

Not done, check these on the server:
- **No real embedding model has been used**, only a stand-in. Run
  nomic-embed-text v1.5 on llama-server with `-b 2048 -ub 2048` (a passage
  longer than the batch fails) and check that "Search by meaning" shows on
  the Workspaces page.
- **No real chat model has answered from passages.** Check that Llama 3.1 8B
  and Gemma 4 12B cite [n] and say so when the documents do not cover a
  question. The top 6 passages are always sent, even weak ones; if answers
  drift, lower `top_k`.
- Speed of adding a large PDF (hundreds of pages) with a real embedding model.
- Scanned PDFs have no text and are refused, as in the chat.
- There are no logins yet: anyone who can open the app can see and delete
  every workspace.

### Agent skills (charts, memory) and the Admin panel

Modelled on AnythingLLM's agent skills (`rechart`, `memory`) and its Admin ->
Agent Skills page. Code: `skills/agent_skills/` (package `agent_skills`, no
model in it), `backend/app/admin/routes.py`, `frontend/admin.html`.

- **Charts.** The model calls `create_chart` with the kind (bar, line, area,
  pie), labels and series; `charts.py` checks the numbers ("₹1,200" becomes
  1200, a series must have one value per label) and the chat draws the chart
  as SVG, with no chart library, so it works offline. A wrong call is sent back
  with the reason so the model can fix it.
- **Memory.** Short facts per workspace, plus a "general" list for chats
  without one, in `backend/data/admin/memories.json`. Every chat answered by a
  local model gets its scope's memories in the system message (never a cloud
  model). Admins add, edit and remove them on the Admin panel; in the chat the
  model saves or forgets one only when the user asks (`save_memory`,
  `forget_memory`), if "Save and forget from the chat" is on. When several
  memories could be meant, nothing is forgotten and the model is told to ask.
- **Switches** (`backend/data/admin/skills.json`): Database queries, Charts,
  Memory, Save and forget from the chat. All on by default. With Database
  queries off, the chat hides the database picker and `/api/chat` refuses a
  database (403).
- **A skill is offered only when the message needs it** (`toolbox.py`): charts
  with a database or workspace picked or when a chart is asked for; the memory
  tools when the message says remember or forget. Small models call tools
  they do not need, so an everyday chat gets none. A message that does get a
  skill is answered by `sql.model` (the local model that calls tools).
- **The Admin panel needs `ADMIN_PASSWORD`** in `backend/.env`. Without it
  the panel is closed (403); a wrong password waits a second. It stands in
  for users and roles, which do not exist yet. The Databases and Workspaces
  pages are still open to everyone.

On the server: set `ADMIN_PASSWORD`, restart, open
<http://127.0.0.1:8000/admin.html>. Check without a model:
`cd backend && python -m agent_skills`.

Not done, check these on the server:
- **No real model has called `create_chart`, `save_memory` or
  `forget_memory`**, only a stand-in. Check that Llama 3.1 8B copies the
  numbers exactly and saves a memory only when asked.
- Charts were looked at in Chromium only.

### Fixed in this round (review of the branch)

- **SQL, critical:** on PostgreSQL a crafted query could run a write. psycopg
  runs every statement of a query sent without parameters, and the text check
  read `#` and `\'` the MySQL way, so `SELECT 1 # x; COMMIT; SET
  default_transaction_read_only = off; DELETE FROM orders` passed it. Now
  PostgreSQL queries go as prepared statements (one statement only), the
  check reads strings as each database does and refuses comments, and MySQL
  sessions are read-only too. The bypasses are in `python -m sql_connector`.
- Workspaces with names in Kannada, Hindi or with accents could not be
  created; ids are now ASCII (`workspace-<random>` when nothing is left).
- A picture sent alone in a workspace no longer searches for nothing.
- `python -m ollama_pipeline` no longer fails when only the optional
  embeddings server is down.
- llama-server's "--jinja" and "mmproj" errors now say what to start it with.
- A database lookup cut off by Stop no longer shows "Running a query…".
- **Documents pages:** the six tabs are gone. `documents.html` opens on four
  tasks (Create a new document, Fill in a template, Change a document, Read
  invoices into a sheet); each task shows its steps and what the button is
  waiting for. "Write a new one" was folded into Create a new document
  (`word.html`, which has the letterhead too); saved templates are offered
  right where a template is chosen. `documents.html#create` goes to
  `word.html`. The chat's menu lost its two placeholder links.

UI fixes in this branch: the chat composer's text, chips and model name no
longer shrink below a readable size on laptop screens (floors, positions
unchanged); the model menu is wider and shows whole descriptions; selected
items show a tick and finished Thinking / lookup blocks a chevron instead of
more dots; status dots only show while something is running; the documents
pages' small text is a size larger and their buttons taller with more padding,
and a disabled button looks switched off.

## Running the `11-word` branch on the server

This branch adds the **Word document** tab to the documents page: several
source files in (PDF, Word, Excel, CSV, text), one designed Word document out,
downloaded as Word or PDF. It contains `10-invoice`. On a machine that already
runs the app:

1. **Get the branch.** `git fetch && git checkout 11-word`
2. **Install one new package** (`mdit-py-plugins`; `markdown-it-py` and
   `PyYAML` come with it or are already there):
   `pip install -r requirements.txt` from the repo root, or with uv
   `cd backend && uv sync --extra router --inexact`. Without it the app still
   starts; the tab then says which packages are missing.
3. **Nothing to add to `models.yaml`.** The tab uses the `documents:` block
   that is already there (`documents.model`, `qwen3:14b`).
4. **For the PDF download, LibreOffice** must be on the server
   (`soffice --version`). Without it the tab offers the Word file only and
   says why. Ask the user before installing it.
5. **Restart** and open <http://127.0.0.1:8000/word.html>, or Docs -> Word
   document. Refresh the browser with Ctrl+Shift+R.
6. **Check it without a model:** `cd backend && python -m document_engine.word`
   must print that sources, sharing, building and letterhead all pass.

What to check is under "Word document tab" below, in "Not done".

## Running the `10-invoice` branch on the server

This branch adds the **Invoices** tab to the documents page: invoices in (PDF,
Word, scans, photos), an Excel or CSV sheet out. It contains `6-documents` and
`8-testing`. On a machine that already runs the app:

1. **Get the branch.** `git fetch && git checkout 10-invoice`
2. **Install three new packages** (`pypdfium2`, `pillow`, `openpyxl`):
   `pip install -r requirements.txt` from the repo root, or with uv
   `cd backend && uv sync --extra router --inexact`. Without them the app still
   starts; the Invoices tab then says which package is missing.
3. **The model must read pictures.** `ollama/config/models.yaml` names
   `qwen3.5:9b` (entry `id: invoices`). `ollama show qwen3.5:9b` must list
   `vision` under Capabilities. `gemma4:12b` reads pictures too and can be
   picked on the page to compare.
4. **If this machine has `ollama/config.local/models.yaml`,** copy the
   `invoices:` block and the `- id: invoices` model entry into it. Without them
   the tab still works: it starts with the first local model that reads
   pictures (by name, so `gemma4:12b` before `qwen3.5:9b`).
5. **Restart** (`cd backend && python -m app`, or `./run.sh`) and open
   <http://127.0.0.1:8000/invoices.html>, or Docs -> Invoices. Refresh the
   browser with Ctrl+Shift+R: it keeps the old page otherwise.
6. **Or from a terminal,** a whole folder at once:
   `cd backend && python ../documents/read_invoices.py /path/to/invoices --out out.xlsx`
   Add `--model gemma4:12b` to compare models on the same files.

What to check is under "Invoices tab" below, in "Not done".

## Running the `6-documents` branch on the server

This branch adds the Word documents page. On a machine that already runs the
app, these are the only steps:

1. **Get the branch.** `git fetch && git checkout 6-documents`
2. **Install the new packages** (the project's own `documents/` package and
   `python-docx`, which brings `lxml`):
   `cd backend && uv sync --extra router --inexact`
   Without uv: `pip install -r requirements.txt` from the repo root.
3. **Pull the documents model:** `ollama pull qwen3:14b` (about 9 GB).
4. **If this machine has `ollama/config.local/models.yaml`, update it.** That
   file replaces `ollama/config/models.yaml` completely, so the new parts must
   be copied into it or the page starts with the wrong model:
   - the whole `documents:` block (with `model: documents`, `allow_cloud: false`);
   - the model entry `- id: documents` (`qwen3:14b`) under `models:`.
   No `config.local`? Nothing to do: `ollama/config/models.yaml` already has both.
5. **Check, then restart.** `cd backend && uv run --inexact python -m ollama_pipeline`
   must list `documents  qwen3:14b  ready`. Then stop the app and start it
   again (`./run.sh`, or `run.ps1` on Windows).
6. **Open the page:** <http://127.0.0.1:8000/documents.html>, or the **Docs**
   link on the main page. The model picker on it should show "Qwen 3 14B".

Memory: `qwen3:14b` takes about 9 GB before any context, so it will not sit on
a 16 GB GPU next to both chat models. Ollama swaps models in and out as needed
(slower first reply after a swap). Check `ollama ps` while testing.

Uploaded and generated files go to `backend/data/documents/` (deleted after a
day); saved templates go to `backend/data/templates/` (kept). Neither is in git.

What to test on this branch is listed under "Word documents page" below,
in "Not done, check these on the server".

## Start here on a new machine

Five steps. "Setup" further down has the details and the reasons.

1. **Python packages.** `cd backend && uv sync --extra router` (needs
   [uv](https://docs.astral.sh/uv/) and Python 3.12+). Without uv:
   `pip install -r requirements.txt` from the repo root.
2. **The two llama-servers** running (Gemma 4 12B on 8080, Llama 3.1 8B on
   8081, both with `--jinja`, Gemma with `--mmproj`). The commands are at the
   top of `ollama/config.llamacpp/models.yaml`. On a machine with no model
   (the development Mac), skip this: the app starts and says the models are
   not reachable.
3. **OpenCode 1.18.34, exactly that version:**
   `npm install -g opencode-ai@1.18.34`, then `opencode --version` must print
   `1.18.34`. Not the package `@opencode/cli`: that is 2.x and does not work.
4. **Models: one file.** On the server, `cp -r ollama/config.llamacpp
   ollama/config.local` (or merge it into an existing `config.local`). Nothing
   else in the project names a model.
5. **Check, then run.** `cd backend && uv run --inexact python -m ollama_pipeline`
   lists both hosts as `ok (llama.cpp ...)` and every model as ready or not. Then `./run.sh` (Windows:
   `powershell -ExecutionPolicy Bypass -File run.ps1`) and open
   <http://127.0.0.1:8000>. The server log should show `Router: Von is ready`
   and `OpenCode: server ready at …`.

## Latest changes — 4 October 2026, on the Windows test laptop

What the last working session added, for whoever picks this up next:

- **Routing.** Questions about software now go to OpenCode: everyday coding
  to a new `code` route, the hardest to `expert`. Everything else stays on the
  local models. Software is recognised by keywords and, failing that, by two
  questions to Von. See "OpenCode routes" below.
- **Routing decisions are logged**, one line each (`Router: …`).
- **Eight free OpenCode models**, several per route, with the next one taking
  over when one fails. What each provider does with prompts is written above
  them in `models.yaml`.
- **OpenCode is pinned and kept apart.** The app starts it with updates off
  and gives it folders of its own (`opencode.home`), so another OpenCode on
  the machine cannot break it.
- **Scrolling.** While a reply is written the chat follows it only if the
  reader is at the bottom; scrolling up is no longer pulled back
  (`follower()` in `frontend/index.html`).
- **`requirements.txt`** for pip, generated from `backend/uv.lock`.
- **`models.yaml`** has a header saying what to edit, with the local models
  first.
- That laptop's own config (`ollama/config.local/`, not in git) uses
  `qwen3-gpu:latest` as the reasoning model and `num_gpu: 99` on the light
  one; see the next section for why.
- Every check in this file was run again there and passes. Still not tested:
  Linux, a 16 GB GPU, `qwen3:14b`, two local models loaded at once.

## Current status — read before trusting anything

- Developed on a laptop without Ollama, then run against real Ollama 0.32.5 on
  a second laptop: Windows 11, GTX 1650 (4 GB VRAM), 16 GB RAM, with
  `llama3.2:3b`, `gemma3:4b`, and `qwen3:4b` standing in for `qwen3:14b`.
  All eleven checks in "What to verify" pass there, with Von 1.3.7 loaded
  (10 by pointing the backend at a port with no Ollama, not by stopping it).
- **Not run anywhere yet:** `qwen3:14b`, a 16 GB GPU, and two routed models
  loaded at once. On 4 GB Ollama swaps models on every route change, so
  nothing measured there says how fast the server will be.
- Left to itself, Ollama puts no routed model fully on a 4 GB GPU at `num_ctx`
  8192: `qwen3:4b` ran 45% on the CPU at 17 tokens/s. Forcing every layer onto
  the GPU fits: `num_gpu: 99` in an entry's `options`, or in the model itself
  as `qwen3-gpu` on that laptop does. Measured: 45 tokens/s for `qwen3:4b`,
  56 for `llama3.2:3b` (37 before). Worth trying on any GPU that is short of
  memory; a model that does not fit this way fails to load.
- So on the server the open questions are about hardware: do the models fit,
  do both routed models stay loaded, and how long do replies take. Expect
  small integration bugs anyway. Fix them, and report what you changed.

## OpenCode routes (cloud) — added after the Ollama test run

With Auto, questions about software go to OpenCode's free cloud models instead
of Ollama: everyday coding to the `code` route, and the hardest (architecture
and system design, hard-to-find bugs such as deadlocks, races and memory
leaks, large refactors) to the `expert` route. Mail, bills, documents and
other reasoning stay on the local models. Code:
`ollama/ollama_pipeline/opencode.py`; config: `opencode:`, the `opencode*`
models and the `code` and `expert` routes in `models.yaml`.

- Tested with OpenCode 1.18.34 and the real free model `opencode/big-pickle`
  on macOS and on Windows 11; checks (a) to (g) below pass on both. **Not
  tested on Linux.** On Windows the server starts from Python and no
  `opencode serve` is left after a clean stop, a crash, or a killed process
  tree (the last one leaves the empty temp folder behind). Not tried: killing
  only the backend process.
- **Use OpenCode 1.18.34; 2.x does not work.** `@opencode/cli` 2.0.22 is a
  different npm package from the same authors. Its `serve` only has
  `/api/...` routes (`POST /session` answers 405), so every cloud model shows
  as unavailable and Auto quietly answers locally. Install the tested version
  with `npm install -g opencode-ai@1.18.34`.
- **If `opencode` on this machine is already a 2.x**, leave it and install the
  old one into a folder: `npm install --prefix <folder> opencode-ai@1.18.34`,
  then set `opencode.command` in `models.yaml` to
  `<folder>/node_modules/.bin/opencode`.
- **1.x and 2.x cannot share OpenCode's default data folder.** Once 2.x has
  run, 1.x stops with "Database is not empty and has no session table". The
  app therefore gives its OpenCode folders of its own: `opencode.home` in
  `models.yaml`, by default `ollama/.opencode/` (not in git, safe to delete).
  It also starts it with `OPENCODE_DISABLE_AUTOUPDATE`, so the installed
  version is the one that runs.
- How a message gets to the cloud (`router.py`), in this order:
  1. Clear signs of software (`_SOFTWARE`: language names, code snippets,
     pasted commands, file names like `app.py`, "write a code for", "fix this
     bug", "git commit") send it there without asking Von.
  2. Otherwise Von is asked whether the request is about software
     (`_von_says_software`): two questions, and it must pass both. This is
     what catches plain wording such as "make a landing page for my coffee
     shop" or "can you build me a snake game".
  3. `_EXPERT` then picks `expert` over `code`.
  Everything else stays local, where Von chooses between `simple` and
  `complex` as before. The server log has one line per decision, e.g.
  `Router: code -> opencode-space-bunny (von, 0.902)`: read it first when a
  message went somewhere unexpected.
- Why two questions and not a third choice for Von. With `code` or `expert`
  added to its choices Von was unsure on more than half of the sample
  requests, so keyword rules decided and coding requests in plain words went
  to the local model. One yes/no question alone was either too eager (it
  called "hey" software) or too cautious. Passing both, on 35 samples: 15 of
  16 software requests caught, 2 of 19 others let through (both asked for a
  "script", for a film and for a school event). Each question costs Von about
  0.4 s on a laptop CPU, so routing takes up to about a second.
- `_SOFTWARE` is narrow on purpose: an everyday message it matches leaves the
  machine without Von being asked, so words that are also everyday words
  ("class", "error", "script", "java") are not in it.
- **Software is checked before stickiness.** A code question goes to the cloud
  even when a local model answered the message before it. A message with no
  software word of its own ("add error handling to it") also goes there when
  the chat is already about software: the request before it was a software
  one, the last reply contains a code block, or a cloud model answered last
  (`_cloud_route`, `_in_software_thread`; the reply header says "Routed by
  context"). Requests for an email, translation or summary (`_EVERYDAY`) do
  not follow the thread and go back to a local model. The "stay on the model
  already answering" rule now applies to local models only.
- When a software question stays local because the chat has attachments, the
  reply carries a note saying so. Without it this looks like a routing bug.
- Checks: after a local reply ask "now write it in html" and "can you code
  this" → OpenCode; after a reply with code ask "add error handling to it" →
  OpenCode ("context"); after an OpenCode reply ask "write an email to my boss
  about it" → local light model; attach a PDF, then ask for code → local, with
  the note.
- Each cloud route lists several models (`model: [a, b, c]`). The first one
  OpenCode offers answers. If it fails, the next answers and the reply says
  so; after the last, the local heavy model. With another model lined up the
  first failure is enough and OpenCode's own retries are not waited for: a
  broken first model cost under 3 s in testing, against 75 s.
- Models, tested 2026-10-04 with a coding task and a bug hunt: eight of the
  nine free models that are not previews answered both correctly.
  `ling-3.0-flash-fin-free` answered "Endpoint is unavailable" and is left out,
  like `longcat-2.5-preview-free`. The free list changes; `opencode models`
  shows the current one, and an id that is gone shows as unavailable.
- **What the providers do with prompts differs** (https://opencode.ai/docs/zen
  on that date). Space Bunny keeps nothing, which is why it is first in the
  `code` route. Big Pickle, MiMo, Ling and Fledge "may be used to improve the
  model". Nemotron is logged. Muse Spark trains future Meta models, so it is
  in the picker but in no route. Check these before changing a route's order.
- The backend starts its own `opencode serve` (127.0.0.1, random port, random
  password, empty temp folder) at startup and talks to it the way OpenCode's
  own `opencode run` does: create a session, send the prompt, read the event
  stream. The log prints `OpenCode: server ready at …` when it is up.
- **Replies stream live.** Text comes from `message.part.delta` events and
  reaches the browser as it is written; first words usually within 5-10 s.
  On an open question `big-pickle` can reason for over a minute first (87 s
  for check (a) on one run); the Thinking block shows that as it happens.
  Each reply is a throwaway session that is aborted on Stop and then deleted.
- **Do not customise the agent, its prompt, or which tools exist.** OpenCode's
  service answers "free tier can only be used from within OpenCode" (403) for
  anything but a built-in agent. Do not work around that by faking a client.
- Shell, edit, web and outside-folder access are set to permission "ask", and
  the client answers every `permission.asked` event with "reject" (plus a
  message so the model carries on in text). OpenCode therefore cannot run
  commands, change files, fetch URLs or read outside its empty folder. A
  request that is never answered stays pending; nothing is approved by
  default. Keep it so, and never reply "once" or "always". Every tool attempt
  is logged (`OpenCode: rejected its request to use 'bash'`).
- Privacy rules, keep them: attached files and images are never sent; in Auto
  a conversation that has had any attachment stays on local models.
- If OpenCode is missing, refuses, errors or times out, the local heavy model
  answers and the reply says so.
- If `ollama/config.local/models.yaml` exists on this machine it overrides
  `ollama/config/` — copy the new `opencode:` block, the `opencode` model and
  the `expert` route into it, or the route will not exist here.

Checks: (a) "design the architecture for a chat service" → header shows
"Auto · OpenCode Big Pickle" and the text grows as it is written, with an
orange caret at the end until it finishes; (b) follow with "make it shorter" →
stays; (c) same question with a PDF attached → local reasoning model;
(d) rename the command in config to something that does not exist → local
model answers; (e) press Stop mid-reply → text stops at once; (f) pick
OpenCode in the picker and ask it to "run `uname -s` with your shell tool" →
the server log shows the rejection and the reply contains no real output of a
command; (g) stop the backend → `opencode serve` is no longer running;
(h) "write a python function for binary search and explain its complexity" →
"Auto · OpenCode Space Bunny", and so does "make a landing page for my coffee
shop" (tooltip: "Routed by von"); (i) follow with a longer request for an email →
a local model answers; (j) put a model that fails first in a route
(`opencode/ling-3.0-flash-fin-free` did on the test day) → the next model
answers within seconds and the reply says which one could not. An id
OpenCode does not offer at all is skipped without a note.

## Live generation in the UI

Nothing should "pop out in one go". What the user sees while a reply is made:

- **Answer text** grows as it arrives, for Ollama and OpenCode alike, with an
  orange caret at the end until the reply is complete.
- **Thinking** (models that reason first, e.g. `qwen3`): the reasoning text
  streams into a "Thinking…" block above the answer. When the answer starts
  the block folds to "Thought for Ns"; clicking it opens it again.
- Before anything has arrived, three dots.

Where it lives: the backend sends `thinking` and `delta` events
(`pipeline.py`, `opencode.py`); `send()` in `frontend/index.html` paints them
once per animation frame. A browser tab in the background only repaints about
once a second, so judge smoothness with the tab visible.

Check on this machine: ask `qwen3` a coding question and watch the Thinking
block fill, then fold; the answer must appear word by word, not as one block.
If a reply does arrive in one piece, look at what sits between browser and
backend (a proxy that buffers) before changing the code.

## Word documents page (work in progress)

`frontend/documents.html`, reached from the **Docs** link on the main page.
Tabs: fill a template, change a document, write a new one, the template
library (result: a downloadable `.docx`), Word document (several sources into
one designed document, as Word or PDF, see "Word document tab" below), and
Invoices (result: a sheet, see "Invoices tab" below).

- **The model never writes the Word file.** It answers in JSON (which blank
  gets which value, or which edit operations to run) and the app's own code
  applies that to a copy of the uploaded file, so the rest of the file is
  untouched. The feature has folders of its own, see "Where the documents
  code is" just below.
- **Local models only.** `documents.allow_cloud` in `models.yaml` is false and
  must stay false on the server; it exists only to try the page on a machine
  without Ollama.
- The user reviews every suggested value before the file is made. Values are
  tagged by where they came from (their files, their instructions, or written
  by the model).
- Files are kept in `backend/data/documents/` under random ids and deleted
  after `documents.keep_hours`.
- Needs `python-docx` (in `requirements.txt` and the uv lock). Accepts `.docx`
  and `.dotx`; refuses old `.doc`, macro files and password-protected files.

### Where the documents code is

Separate from the chat code. Nothing for this page lives in `ollama/` or in
`backend/app/routes.py`.

```
documents/                         the feature itself (package `document_engine`)
  document_engine/
    engine/                        works on Word files. No model.
      wordfile.py                    open, number paragraphs and text boxes, find blanks, fill
      wordedit.py                    the fixed list of edit operations; Markdown -> Word
      slotmap.py                     labels of a designed template -> its slot map
      render.py                      fill a designed template, copy/remove repeated blocks
    model/                         the model's part. It decides, it never writes a file.
      prompts.py                     every instruction the model gets + the JSON it must answer in
      service.py                     sends them to the local model, checks the answers
    invoices/                      the Invoices tab, apart from the Word code
      reader.py                      a file -> pages (text and/or a picture). No model.
      checks.py                      clean, check, score the confidence. No model.
      export.py                      rows -> .xlsx / .csv. No model.
      prompts.py                     the model's part: what it is told, the JSON it answers in
      service.py                     the model's part: one page at a time to the local model
    word/                          the Word document tab, apart from both
      sources.py                     a source file -> text; Excel sheets as tables. No model.
      renderer.py                    Markdown -> a designed .docx (from Generate Documents, MIT). No model.
      build.py                       the page's choices + the Markdown -> the file. No model.
      prompts.py                     the model's part: what it is told
      service.py                     the model's part: sources to the local model, the text streamed back
  read_invoices.py                 the same reading from a terminal, for a folder of files
backend/app/documents/             the HTTP side
  routes.py                          /api/documents/... endpoints
  storage.py                         uploads, versions, the template library on disk
  schemas.py                         request bodies
  invoices.py                        /api/documents/invoices/... endpoints (stores nothing)
  word.py                            /api/documents/word/... endpoints
frontend/documents.html            the page
frontend/word.html                 its Word document tab
frontend/invoices.html             its Invoices tab
```

What is shared with the chat side, on purpose: the model registry and Ollama
client (`ollama_pipeline`), the `documents:` block and model list in
`ollama/config/models.yaml`, and `UnsupportedDocument`. To change what the
model is told, edit `model/prompts.py` only. To change what happens to a
file, edit `engine/`.

### Designed templates (a resume, a report with a fixed look)

Such a file has no blanks: it is full of sample text and its layout lives in
the body (tables, text boxes). It goes through the **Template library** tab:

1. **Prepare, once.** The model labels every line: `fixed` (a heading that
   stays), `field` (one value), or `group_item` (part of a repeating block:
   job 2's title, its bullets). A person reviews the colours on the page,
   corrects by clicking a line, and saves. Stored in
   `backend/data/templates/<id>/` (template.docx, labels.json, slotmap.json);
   not deleted by `keep_hours`.
2. **Fill, any number of times.** The model moves the user's content into the
   slot map's shape (`docgen.extract`), the user reviews it, and
   `render.py` writes it into a copy of the template: repeated blocks are
   copied for more items and removed for fewer, text boxes are written in both
   their copies, skill-chip boxes can be dropped but not added.

Code: `engine/slotmap.py` (labels -> slot map, JSON schema for the model),
`engine/render.py` (fill + clone/remove), `prepare` and `extract` in
`model/service.py` with their instructions in `model/prompts.py`.

The label view shows the whole template and scrolls with the page; its
controls are docked at the bottom of the screen (`.dock`). Do not put the
template back into a fixed-height box: on a 12-page file that reads as stuck.

Rules to keep:
- "Write a new one" never replaces the body of a designed template
  (`WordFile.is_letterhead()`); it only writes onto a real letterhead.
- Text boxes are numbered `t1`, `t2`, ... (modern copy only);
  `WordFile.save()` copies changes into the fallback copy. Do not resize shapes.
- JSON calls send `think: false` to thinking models (`ModelEntry.thinking`).
- The documents model is `documents.model` in `models.yaml` (`qwen3:14b`).

Status. Tested without a model, on the brown two-column resume template:
text boxes read and written in both copies; slot map built from hand labels;
a made-up resume with 4 jobs (2-5 bullets), 2 schools and fewer skills
rendered with the design kept and no sample text left in the XML; a
follow-up edit changed only the profile box; library routes; the refusal to
write over a designed template. Looked at with macOS Quick Look only.

**Not done, check these on the server with `qwen3:14b`:**
- `prepare` and `extract` have never been run against any model. Count how
  many of the brown template's 37 lines the model labels right before
  correction (expected labels: fields name, title, phone, email, address,
  linkedin, summary; groups jobs x3, education, skills, additional_skills;
  six fixed headings).
- The Template library tab has not been clicked through in a browser.
- No file was opened in Word or LibreOffice. Open one result in Word and
  confirm there is no repair prompt (copied text boxes get new drawing ids).
- LibreOffice is not installed here, so the PDF preview path
  (`_pdf` in `backend/app/documents/routes.py`) is untested; it is skipped when
  `soffice` is missing.
- Only the resume template was tried; a second kind (report, letter with a
  table) still needs to go through prepare -> fill.

### Word document tab (several sources into one designed document)

`frontend/word.html`, a tab of the documents page. Sources in: PDF, `.docx`,
`.xlsx`/`.xlsm`, CSV, text. Out: a `.docx` built by the app, and a PDF of it.

What it has, all chosen on the page: seven kinds of document (report,
proposal, white paper, memo, minutes, letter, plain), one colour that the whole
design is built on, a cover page, a contents page, an author line, a company
letterhead (header, footer and margins kept, body replaced). In the text:
numbered headings, tables with a coloured header and right-aligned figures,
callout boxes (info, success, warning, danger), checklists, quotations, code
blocks, signature blocks, page breaks, highlighted figures.

How a document is made (`documents/document_engine/word/`):

1. `sources.py` (no model) turns each file into text. PDF, Word and text go
   through the chat's `extract_text`; an Excel sheet becomes a Markdown table.
2. `service.py` + `prompts.py` (the model's part) send the instructions and
   all the sources to the local model, which writes the document as Markdown.
   It streams to the page as it is written.
3. `build.py` (no model) puts the page's choices on top of that text and
   `renderer.py` (no model) writes the Word file.
4. The file is an ordinary job of the documents page: downloaded through
   `/api/documents/{job}/download`, changed through `/api/documents/{job}/edit`.
   `/api/documents/word/{job}/pdf` converts the newest version with LibreOffice.

The text stays on the page and can be edited. "Build again" makes the file
from it with another kind, colour or pages, without asking the model.

Where the renderer comes from: `renderer.py` is "Generate Documents" 1.2.4 by
IANUSTEC, a tool written for Open WebUI (MIT, licence file beside it). It was
cut down by script: everything that talks to Open WebUI and everything that
goes to the network (Unsplash, image generation, pictures fetched from a URL)
is gone. The design system, templates, Markdown parser and renderers are
unchanged, so its README still describes what the Markdown may contain.

OfficeCLI (github.com/iOfficeAI/OfficeCLI) was read and **not** integrated.
It is a compiled .NET program that updates itself and installs agent skills on
the machine it runs on, so it was not downloaded or run here. Most of what it
does for Word this project already does in Python: edit operations, filling
a template's blanks, contents page, headers and footers. Its HTML and PNG
rendering of a `.docx` is the one thing that would be new.

Rules to keep:
- **Local models only, always.** There is no cloud path in `word/service.py`
  and `documents.allow_cloud` does not apply to it. The page never sends a
  request without a local model picked.
- **The model writes Markdown, never the Word file.**
- **Nothing is fetched.** A picture the model links to is left out.
- **The page's choices win** over anything the model wrote in the frontmatter.
  A letterhead is only ever the file the user attached, never a name in the text.
- **A designed template is refused as a letterhead** (`WordFile.is_letterhead()`),
  before the model starts.
- **Each source gets an equal share of the context** (`share()` in
  `service.py`); what a short file does not use goes to the longer ones. A
  file that is cut is named in a note on the page.
- The contents page lists the section names as text. Word replaces that with
  the real contents and page numbers when the file is opened; a PDF keeps the
  names without page numbers (`_list_contents` in `build.py`).

Status. On this laptop there is no Ollama, so the model was a stand-in that
streams a hand-typed document. Run that way: reading every source type, the
page in a browser (add files, write, edit the text, build again with another
kind and colour, the change box, both downloads), letterhead, the refusal of
a designed template, the PDF through LibreOffice 26.8 on macOS, and
`python -m document_engine.word`. The upstream example `report.md` builds to
a file of the same size as upstream's own `demo_report.docx`.

**Not done, check these on the server:**
- **No real model has written a document.** Check that `qwen3:14b` keeps to
  the Markdown in `prompts.py`: the `---` title lines first, callouts written
  as `::: callout`, figures from an Excel sheet put in a table and copied
  exactly. A model that ignores the title lines still gives a document: the
  first `# ` heading becomes the title.
- Several long sources at once: how much of each fits at `num_ctx` 16384, and
  whether the note about cut files appears.
- No built file was opened in Word itself, only in LibreOffice and python-docx.
  Open one with a contents page and confirm Word offers to update it.
- PDF: fonts. The templates ask for Calibri; LibreOffice on a server without
  it substitutes another font and the line breaks move.
- Two PDF requests at the same moment can fail (one LibreOffice profile);
  the second click works. Noted as a shortcut in `backend/app/documents/word.py`.
- Scanned PDFs and photos are not read here. The Invoices tab reads pictures;
  this tab says so and refuses them.

### Invoices tab (invoices into a sheet)

`frontend/invoices.html`, the fifth tab of the documents page. Files in: PDF,
`.docx`, JPG/PNG/WEBP/BMP/TIFF. Out: `.xlsx` or `.csv` with exactly these
columns, in this order: Invoice File Name, InvoiceId, Invoice Date, DueDate,
InvoiceTotal, VendorName, VendorAddress, CustomerName, CustomerId,
BillingAddress, BillingAddressRecipient, VendorAddressRecipient, VendorGST,
CustomerGST, Description, Qty, UnitPrice, UnitAmount, Discount, TaxableValue,
CGSTAmount, SGSTAmount, IGSTAmount, TotalTaxAmount, HSNSAC, Currency,
DocumentType, Confidence. Confidence is last, as asked.

**One row for each line of an invoice.** A bill with seven items is seven rows:
Description, Qty, UnitPrice, UnitAmount and HSNSAC are the line's own. The
invoice's fields (number, date, seller, buyer, GST numbers, total, taxes, Confidence)
and the file name stand on its first row, as on the page; the rows after it
carry only the line. Confidence is written as a percentage (80%). Tick boxes under
the sheet add: a Summary sheet (one row for each invoice), the Checks sheet, a
totals row, the invoice's details on every row, its totals on every row.
A document with no item table (a taxi receipt, a payment screenshot) is one row.

How a file is read (`documents/document_engine/invoices/`):

1. `reader.py` (no model) turns the file into pages. A PDF page with real text
   gives its text, laid out as on the page, **and** a picture of the page. A
   scan or photo gives a picture only. A Word file gives its text, and each
   large picture inside it as a page.
2. `service.py` + `prompts.py` (the model's part) send one page at a time to
   the local model, which answers in JSON: the invoices, receipts and payment
   confirmations on that page. One file can give several rows (a claim with
   three bills, 24 taxi receipts).
3. `checks.py` (no model) cleans each value, checks it, and works out the
   Confidence. Pages of one invoice are merged into one row.
4. A person reviews the rows on the page, then `export.py` writes the sheet.

Rules to keep:
- **Local models only, always.** There is no cloud path in this code and
  `documents.allow_cloud` does not apply to it.
- **Nothing is stored.** A file is read in memory and forgotten; the rows live
  in the browser until downloaded.
- **The model never sees the file name.** The names here start with an upload
  time that reads like a date. The earlier attempt returned a wrong invoice
  date for a scanned file whose only text was a print header and that name.
- **A PDF page with under 200 characters of text is treated as a scan**
  (`MIN_TEXT`): what text it has is a print header or footer.
- **Nothing the model returns is trusted as is.** `Confidence` is a score from
  checks, not a probability: a value found in the file's own text, a GSTIN
  whose check digit is right, or a total that matches the amount in words
  counts 1.0; a value read from a picture with nothing to confirm it 0.8; a
  failed check or a value the model marked unclear 0.35. The weights are at
  the top of `checks.py` and reach the page through `/config`.
- Dates are read day first (Indian invoices) and written as `31-Mar-2026`
  (`invoices.date_format`). A due date is never worked out from payment terms.
- A PAN is not put in a GST column.
- **The tax breakdown must add up.** Taxable value (less a discount) + tax =
  total, within a rupee of round-off. When it does, those figures count as
  confirmed, on a scan too. When it does not, the figures the file's own text
  does not confirm are marked, with the sum in the note. An invoice with a
  charge that is neither (freight billed outside the taxable value) is marked
  as well: that is a prompt to look, not an error.
- `TotalTaxAmount` is not asked of the model: it is CGST + SGST + IGST, added
  here. A page with one tax figure and no split keeps that figure. CGST and
  SGST that differ, or IGST beside them, are marked. A rate ("9%") is never
  taken for an amount; a tax printed as 0.00 or a dash is left empty.
- `Currency` is read off the sign or code the model copied (₹, Rs, INR,
  "Rupees ... Only"), `DocumentType` is what the model called the page
  (Invoice, Receipt, Payment). Neither counts towards Confidence.
- **The lines can confirm, they do not mark.** Quantity x unit price making the
  line's amount, and the lines adding up to the total (or the total before
  tax, or the taxable value), count as confirmed. When they do not, no cell is
  marked; a sum that is off is said once in a note. The second server run
  (1,345 rows, 9 October) had 355 line amounts marked, mostly bills whose
  lines carry discounts. The Checks sheet lists only values marked for
  checking and notes on unconfirmed values, not fields that are simply absent. Lines for totals and
  taxes that the model lists as items are dropped (`_NOT_AN_ITEM`).
- A row is finished in `merge` (`finish`), after the pages of one invoice are
  together: only then are all its lines known. `clean` alone returns a row
  with no Confidence yet.
- Seen in the first real run, and handled in `checks.py`: the invoice total
  entered as a tax (dropped); one tax amount entered as both CGST and SGST
  (moved to IGST when only one such amount fits the total); payment terms
  entered as a due date (left empty); a year more than three years back
  (marked).
- Text in the sheet is written as text, never as a formula: invoices come from
  outside the company.

Settings: the `invoices:` block in `models.yaml`. To change what the model is
told, edit `invoices/prompts.py` only.

Status. On this laptop there is no Ollama, so everything here was run against
a stand-in that returns hand-typed answers: reading every file type, the
checks (on the real GSTINs, dates, totals, amounts in words and line items of
the samples), merging, the page in a browser (add, read, stop, edit, add and
remove a line, tick, remove, both downloads), the Excel and CSV files, the
error messages, and `python -m app` without the project's packages installed.

On the server the user ran it with a real model on 8 October 2026 and
reported that it works. Their sheet from that run is what the line-by-line
rows, UnitPrice and UnitAmount, and the fixes listed above come from.

**Not done, check these on the server:**
- **The line-by-line reading has not been run by a real model.** The model is
  now asked for a list of lines (`Items`) where it gave one description
  before. Check that it gives one entry for each line, with the unit price and
  the amount in the right places, on the two files this was built from: a
  printed cash bill with seven items, and a photo of a handwritten bill.
- A page's answer is longer now (a line is about 50 tokens), so pages with
  many lines take longer; `MAX_ANSWER_TOKENS` in `service.py` is 6000.
- Speed per page and memory on the 16 GB card (`ollama ps`).
- Handwriting. Expect mistakes there; the line sums, the Confidence and the
  orange marks are what should catch them.
- If small print is misread, raise `invoices.image_side` to 2000.
- No downloaded file was opened in Excel itself, only read back with openpyxl.
  A CSV made from the Excel file with Excel's plain "CSV" turns Kannada and
  other scripts into question marks; the page's own Download CSV keeps them.

## Your task on this machine

1. On the server: get the app running against the two llama-servers (Setup
   below). On the development Mac: there is no model, so run the self-checks
   and test through the API and the browser instead.
2. Run the checks in "What to verify" and note pass/fail for each.
3. Fix what fails, keeping to "Rules".
4. Report back: hardware, models pulled, what passed, what failed, what you
   changed, and anything you could not test.

## Setup

Requirements: Python 3.11+ (3.12+ for Von), [uv](https://docs.astral.sh/uv/),
on the server llama.cpp's `llama-server` with the Gemma 4 12B and Llama 3.1 8B
GGUF files, and for the cloud routes Node.js with OpenCode 1.18.34. Ask the
user before installing llama.cpp or downloading models: they are several GB
each.

```bash
cd backend && uv sync --extra router    # Python packages, with the Von router
npm install -g opencode-ai@1.18.34      # OpenCode, the tested version
cp -r ollama/config.llamacpp ollama/config.local   # on the server, once
# start both llama-servers with the commands at the top of that models.yaml
./run.sh                     # http://127.0.0.1:8000
```

Without uv, `pip install -r requirements.txt` from the repo root installs the
same pinned versions (tested in a fresh environment on Windows), and
`cd backend && python -m app` starts the server. The versions in use on the
test laptop: Python 3.13, uv 0.11, Node 24, Ollama 0.32.5, OpenCode 1.18.34,
Von 1.3.7.

`run.sh` needs bash and `lsof` (macOS/Linux/WSL). On plain Windows run
`powershell -ExecutionPolicy Bypass -File run.ps1` instead.

Downloads go to the user's home folder by default. If that disk is short of
space, set `UV_CACHE_DIR` before `uv sync` (packages) and `HF_HOME` in
`backend/.env` (Von's weights). llama-server loads the GGUF file named by its `-m`.

Check hosts and which configured models are pulled:

```bash
cd backend && uv run --inexact python -m ollama_pipeline
```

### Models: one file

`models.yaml` is the only place that names models: the hosts, the model
names, the OpenCode ids, and which route uses which. On the server it is
`ollama/config.local/models.yaml`, copied from `ollama/config.llamacpp/`.
`ollama/config/models.yaml` is the older Ollama setup, kept for a machine that
runs Ollama. `python -m ollama_pipeline` shows which folder is in use and what
is ready.

### Fit the models to this machine's hardware

Check GPU memory first (`nvidia-smi`, or system info on a Mac).
The server uses `ollama/config.local/`, a copy of `ollama/config.llamacpp/`:
it is ignored by git and used automatically when present. The server log and
`python -m ollama_pipeline` print which folder is in use. In `models.yaml`:

- With llama-server, the context size is its `-c` flag (divided by `-np`
  slots), not `num_ctx`; the app reads it from `/props`. Do not go below
  8192 tokens per request unless necessary: attached documents get cut off.
- Both models fully on the GPU leave about 1 GB for context on 16 GB. See
  "llama-server on the RTX 4080 Super" above for the flags (`-fa on`, a q8 KV
  cache, `--kv-unified`) and the trade-offs.
- The target production server is an RTX 4080 Super (16 GB VRAM), 32 GB RAM,
  i7-13700K. The light and reasoning models must fit in 16 GB together.

### Optional: the Von router

```bash
cd backend && uv sync --extra router    # Python 3.12+, PyTorch, ~3 GB weights on first start
```

Restart and read the server log: it prints either `Router: Von is ready` or
why it fell back to keyword rules. Von is English-only and runs on CPU
(`router.device` in `models.yaml`).

## What to verify

Send these with **Auto** selected. Each reply's header shows the model used
("Auto · <model>"); hover it to see why.

| # | Do this | Expected |
| - | ------- | -------- |
| 1 | "write an email to my manager asking for leave" | light model answers |
| 2 | "review this contract clause and tell me the risks for the tenant: the tenant pays all repairs" | reasoning model answers |
| 3 | After #2, "make it shorter" | stays on the reasoning model |
| 4 | Attach a text PDF, ask a question about its content | reasoning model; answer uses the PDF |
| 5 | Attach a .docx, ask about it | same |
| 6 | Attach a long PDF (30+ pages) | warning about context size appears in the reply |
| 7 | Attach a screenshot, "what is this?" | vision model answers (needs gemma3) |
| 8 | Pick a specific model in the picker, send anything | that model answers, no routing |
| 9 | Press the send button mid-reply | generation stops |
| 10 | Stop Ollama, send a message | clear "Can't reach Ollama" error, no crash |
| 11 | With Von installed: repeat 1–3 | 1 and 2: header tooltip says "Routed by von" with a confidence. 3: "Routed by sticky" |

Also check `ollama ps` while testing: both routed models should stay loaded
and on the GPU. If Ollama unloads one each time the route changes, set
`OLLAMA_MAX_LOADED_MODELS=2` for the Ollama server.

Confirmed with Ollama 0.32.5 and Von 1.3.7:
- `qwen3` is a thinking model. Thinking arrives as separate `thinking` events
  and is shown live in the "Thinking…" block (seen with real Ollama: it
  filled for 61 s, then folded to "Thought for 61s"). On older Ollama
  versions it may arrive inline as `<think>…</think>` text instead, which
  would show up in the answer.
  On slow hardware the thinking can last a minute.
- Vision detection uses `capabilities` from Ollama's `/api/show`, which
  0.32.5 reports.
- `von.decide(...)` returns `.choice` and `.confidence` as the router expects
  and honours `VON_DEVICE`. The first start downloads 3 GB; later starts load
  in under a minute.
- A conversation larger than `num_ctx` is cut from the start, not the end: a
  40-page PDF at `num_ctx` 8192 was answered from its last four pages only
  (Ollama reported 4,098 prompt tokens). The warning appears, but the answer
  does not say what was skipped.

## Where things are

```
frontend/index.html              whole UI (one file)
backend/app/routes.py            API endpoints
ollama/config/models.yaml        every model, route and num_ctx  ← the one file for models
ollama/config.local/             optional per-machine copy of config/, not in git
ollama/.opencode/                the app's OpenCode keeps its own data here, not in git
ollama/ollama_pipeline/
  router.py                      which model answers (Von + keyword rules)
  pipeline.py                    builds the prompt, streams the reply, falls back
  registry.py                    which models exist / are pulled
  opencode.py                    runs OpenCode's server and streams from it
  documents.py                   PDF/DOCX/text → text (chat attachments)
  client.py                      raw calls to Ollama
documents/document_engine/       the Word documents page: engine/ (files) and model/ (prompts, calls)
documents/document_engine/invoices/   the Invoices tab: reader, checks, export, and the model's part
documents/document_engine/word/  the Word document tab: sources, renderer, build, and the model's part
documents/read_invoices.py       invoices from a terminal
sql/sql_connector/               the SQL connector: connectors, safety, saved connections, tools
backend/app/sql/                 its HTTP routes (/api/sql/connections)
frontend/databases.html          its page
ollama/ollama_pipeline/llamacpp.py   llama-server client (hosts with kind: llamacpp)
ollama/config.llamacpp/          models.yaml for llama-server: Gemma 4 12B + Llama 3.1 8B
workspaces/workspace_engine/     workspaces: passages, index (SQLite FTS5 + vectors), search, prompts
workspaces/workspace_engine/chatfiles.py   files attached in the chat: kept 24 h, passages, read in parts
ollama/ollama_pipeline/context.py   fitting every request to the model's real context
sql/sql_connector/sheets.py      Excel/CSV -> SQLite tables the model queries
backend/data/chatfiles/          chat files on disk (not in git)
skills/agent_skills/             agent skills: charts, memory, the switches, the per-message toolbox
backend/app/admin/               the Admin panel's routes (/api/admin, /api/skills)
frontend/admin.html              the Admin panel
backend/app/workspaces/          its HTTP routes (/api/workspaces)
frontend/workspaces.html         its page; the chat's left panel picks a workspace
backend/app/documents/           its HTTP routes and storage
frontend/documents.html          its page
frontend/invoices.html           the Invoices tab
frontend/word.html               the Word document tab
requirements.txt                 pinned Python packages for pip, made from backend/uv.lock
run.sh, run.ps1                  start the app (macOS/Linux, Windows)
```

## Rules

- Calls go one way: `frontend → backend → documents → ollama`. No Ollama-specific
  code in `backend/`; it only calls the `ollama_pipeline` and `document_engine`
  packages. `ollama/` never imports from `documents/`.
- Routing must never block a chat. Any router failure falls back to keyword
  rules; a missing routed model falls back to another one. Keep it that way.
- `frontend/index.html` follows a pixel-exact design. On desktop the composer
  toolbar uses absolute coordinates — do not convert it to flexbox or change
  those numbers.
- Backend or `ollama/` changes need a server restart. Frontend changes only
  need a browser refresh.
- New Python dependency: add it to the right `pyproject.toml`, then
  `cd backend && uv sync --inexact` (plain `uv sync` removes the Von extra),
  and regenerate `requirements.txt` with the command in its header.
- Do not commit `.env`, model weights or the user's documents.
- Work on a branch and open a pull request against `main`; do not push to
  `main` directly.

## Not built yet

No login (the Admin panel has one shared password), chat history only in the browser (not on the server), no text from scanned PDFs, Figma links are
passed as text only, no paid cloud models (e.g. Claude) - only OpenCode's
free tier.
