# DETAIL.md

How the project works today, feature by feature: what the user sees, what the
backend does, and what the Ollama pipeline does underneath. Written from the
code on branch `11-word` (11 October 2026).

`AGENTS.md` says how to set up and test a machine. `PROJECT_FLOW.txt` is the
short map. This file is the long explanation.

## 1. What the app is

A web app that runs on one machine next to Ollama. It has four pages:

| Page | File | What it does |
| ---- | ---- | ------------ |
| Chat | `frontend/index.html` | Ask a model, with optional PDF/DOCX/text/image attachments. "Auto" picks the model for each message. |
| Docs | `frontend/documents.html` | Word files: fill a template, change a document, write a new one, a library of designed templates. |
| Word document | `frontend/word.html` | Several source files (PDF, Word, Excel, CSV, text) into one designed Word document, as Word or PDF. |
| Invoices | `frontend/invoices.html` | Invoices (PDF, Word, scans, photos) into an Excel or CSV sheet. |

Everything runs on local Ollama models, with one exception: in chat, with
Auto, questions about software go to OpenCode's free cloud models. Documents
and invoices never leave the machine.

## 2. The layers

```
browser (frontend/*.html)
   │  HTTP, /api/*  — replies come back as lines of JSON (NDJSON)
   ▼
backend/app            FastAPI. Receives requests, stores uploads, streams answers.
   │  Python calls
   ▼
documents/document_engine    Word files and invoices: read, check, write. Asks the model for decisions.
   │  Python calls
   ▼
ollama/ollama_pipeline       Config, model registry, router, prompt building, Ollama and OpenCode clients.
   │  HTTP
   ▼
Ollama (127.0.0.1:11434)          opencode serve (127.0.0.1, random port)
```

Calls go one way only. The backend holds no Ollama-specific code, and
`ollama/` never imports from `documents/`.

### Technology

- **Frontend:** three plain HTML files, each with its own CSS and JavaScript
  inside. No framework, no build step, no npm. The Inter font comes from
  Google Fonts. Markdown in chat is rendered by a small function of our own
  (`render()`): fenced code, inline code and bold, escaped first.
- **Backend:** Python, FastAPI, uvicorn. `httpx` for calls to Ollama and
  OpenCode. Pydantic for request bodies.
- **Files:** `pypdf` (chat PDFs), `python-docx` + `lxml` (Word),
  `pypdfium2` + `pillow` (invoice pages as pictures), `openpyxl` (Excel),
  `markdown-it-py` + `mdit-py-plugins` + `PyYAML` (the Word tab's Markdown),
  LibreOffice (`soffice`, optional) for PDF.
- **Models:** Ollama for local models; OpenCode 1.18.34 for the cloud routes;
  Von (optional, ~3 GB, CPU) as the routing model.
- **Packages:** `uv` with three `pyproject.toml` files (`backend/`, `ollama/`,
  `documents/`), or `pip install -r requirements.txt`.

## 3. Starting up

`./run.sh` (or `run.ps1`) runs `python -m app` in `backend/`, which starts
uvicorn on `HOST`:`PORT` (127.0.0.1:8000 unless `backend/.env` says otherwise).

`backend/app/main.py`, at startup (`lifespan`):

1. `settings.py` loads `backend/.env` first, so `OLLAMA_HOST` is set before
   the config is read.
2. `load_config()` reads `models.yaml` and `themes.yaml` from
   `ollama/config.local/` if that folder exists, otherwise `ollama/config/`
   (or from `OLLAMA_PIPELINE_CONFIG`).
3. `Registry(config)` is created, and `warm_up()` starts `opencode serve` in
   the background if any model in the config is an OpenCode one.
4. `Router(config.router)` is created, and `warm_up()` loads Von in a
   background thread. The log prints `Router: Von is ready` or the reason it
   fell back to keyword rules.
5. `DocumentService`, `InvoiceService`, the upload `Store`
   (`backend/data/documents/`) and the template `Library`
   (`backend/data/templates/`) are created.

Then the routes are mounted: `/api/...` (chat), `/api/documents/invoices/...`,
`/api/documents/...`, and last the `frontend/` folder at `/`. FastAPI's own
API docs are at `/docs`.

## 4. The one config file

`ollama/config/models.yaml` is the only place that names a model. Its parts:

| Block | What it sets |
| ----- | ------------ |
| `hosts`, `default_host` | The Ollama servers. `OLLAMA_HOST` overrides the default; `OLLAMA_HOST_<NAME>` overrides one host. |
| `discover: true` | Any chat model pulled in Ollama shows in the picker, even with no entry here. |
| `default_options.num_ctx` | How many tokens a model reads: 16384. |
| `router` | Auto-routing: backend (`von` or `rules`), `min_confidence` 0.7, the routes and which model each uses. |
| `opencode` | The command, the agent (`plan`), the timeout (120 s), its data folder. |
| `documents` | The Docs page: model, `max_jobs` 2, `keep_hours` 24, `allow_cloud: false`. |
| `invoices` | The Invoices tab: model, `max_jobs` 1, `max_pages` 40, `image_side` 1600, `date_format`. |
| `default_model` | Used when Auto is off and nothing is picked. |
| `models` | Each entry: `id`, `label`, the Ollama tag (or OpenCode id), host, system prompt, options. |

Models in the file now:

| id | Tag | Used for |
| -- | --- | -------- |
| `general` | `llama3.2:3b` | route `simple`: mail, quick questions |
| `reasoning` | `qwen-fast:latest` | route `complex`: hard questions, attached documents |
| `coder` | `qwen-fast:latest` | picker only, with a coding system prompt |
| `vision` | `minicpm-v:latest` | messages with an image |
| `documents` | `qwen3:14b` | the Docs page |
| `invoices` | `qwen3.5:9b` | the Invoices tab (must read pictures) |
| `opencode`, `opencode-space-bunny`, … (8) | `opencode/...` | cloud routes `code` and `expert` |

`ollama/config/themes.yaml` holds the prompts for the "Today's Theme" chip;
one is picked per day, in order.

## 5. The model registry (`registry.py`)

On every request that needs the model list, the registry:

1. Asks each Ollama host for its pulled models (`GET /api/tags`). A host that
   does not answer is marked down.
2. For each pulled tag, asks `POST /api/show` once and remembers the
   `capabilities` (vision, thinking, completion).
3. Asks OpenCode which models it offers (`opencode models`, remembered for
   10 minutes).
4. Builds one `ModelEntry` per config entry: `available` is true when the tag
   is pulled (or OpenCode offers the id). `vision` comes from the config if
   set there, otherwise from the capabilities.
5. With `discover: true`, adds every other pulled chat model (embedding
   models are skipped) with the id `<host>/<tag>`.

`resolve(id)` turns a picker id into a runnable model or raises a clear
error: "Can't reach Ollama at …", "… is not pulled. Run `ollama pull …`",
"Unknown model".

## 6. Chat

### What the user does

1. The page loads and calls `GET /api/models`. The picker shows "Auto" first
   (when routing is on and at least one routed model can run), then the
   configured models, then discovered ones. The choice is kept in
   `localStorage`.
2. `GET /api/theme` fills the "Today's Theme" chip.
3. Attaching a PDF, DOCX or text file calls `POST /api/files`; the backend
   returns its text (20 MB limit, 200,000 characters kept). An image stays in
   the browser as base64. A Figma link is added as text only.
4. Send calls `POST /api/chat` with `{model, messages}`. The whole
   conversation is sent each time: the server keeps no chat history. The
   browser keeps it: each chat is saved in `localStorage` (`fastshot.chats`)
   after every reply and listed in the sidebar, without attached pictures.
5. Pressing the send button during a reply aborts the request, and generation
   stops.

### What the backend does (`routes.py` → `pipeline.py`)

1. `select_model()`:
   - A specific model picked, or routing off → `registry.resolve()`. No routing.
   - Auto → `router.decide()` (section 7). The chosen model is used if it can
     run; otherwise the rest of its route, then any other routed model. For a
     cloud model, the fallbacks are remembered: the other cloud models of the
     route in order, then one local model.
2. The response is a stream of JSON lines. First a `route` event (which
   model, which route, why, the confidence), then what `chat_stream()` yields.

### `chat_stream()` for a local model

1. If the model cannot read images and the last message has one → error
   "… can't read images".
2. `build_messages()`: the entry's system prompt goes first; each attached
   file is folded into its message as `<file name="…">…</file>`; images are
   passed on only to a model that reads them.
3. Options are merged: `default_options` < the entry's `options` < the
   request's.
4. `_context_notice()`: if the conversation is estimated (3.5 characters per
   token) at over 90% of `num_ctx`, a `notice` event warns that part will be
   cut. Ollama cuts from the start, so the answer then comes from the end of
   the document.
5. `OllamaClient.chat_stream()` posts to Ollama's `/api/chat` with
   `stream: true` and yields each chunk: reasoning as `thinking` events, text
   as `delta` events, and at the end `done` with tokens per second and total
   seconds.

### `chat_stream()` for a cloud model

1. If the last message has attachments, a notice says they are not sent.
2. The reply streams from OpenCode (section 8).
3. If OpenCode fails before any text was shown and a fallback exists, the
   next model answers. A `route` event and a notice say which model could not
   answer. After the last cloud model, the local one answers.

### Events the browser handles

| Event | Effect on the page |
| ----- | ------------------ |
| `route` | Header becomes "Auto · <model>"; hovering shows "Routed by …" and the confidence. |
| `notice` | A note above the reply (context too large, attachments not sent, a model failed). |
| `thinking` | A "Thinking…" block fills live, then folds to "Thought for Ns". |
| `delta` | The answer grows, with an orange caret until it ends. |
| `sources` | A workspace was searched: the passages are listed under the answer, each opening to its text. |
| `tool_call` | A lookup starts: a block above the answer ("Running a query…", "Making a chart…"). |
| `tool_result` | The block shows what came back: the query and its first rows, a chart drawn in place, the memory saved or forgotten. |
| `done` | Speed and time next to the model name. |
| `error` | The reply turns into an error message. |

Painting happens once per animation frame. The chat follows the growing text
only while the reader is at the bottom (`follower()`).

## 7. Auto-routing (`router.py`)

For the latest user message, in this order:

1. **Image attached** → `image_model` (`vision`).
2. **Software → cloud**, if `code_route` and `expert_route` are configured and
   the chat has never had an attachment:
   - clear signs in the message (`_SOFTWARE`: language names, code snippets,
     pasted commands, file names like `app.py`, "fix this bug"), or
   - the chat is already about software (`_in_software_thread`: the request
     before was a software one, the last reply has a code block, or a cloud
     model answered last), unless the message asks for an email, translation
     or summary (`_EVERYDAY`).
   - `_EXPERT` words (architecture, deadlock, race condition, memory leak,
     large refactor) pick `expert`; otherwise `code`.
   - If the chat has attachments, it stays local and the reply carries a note
     saying why.
3. **Short follow-up** ("make it shorter") → stays on the local model already
   answering (`sticky`).
4. **Von**, when loaded:
   - first two yes/no questions: is this about software? Both yes → cloud;
   - then the choice between the local routes `simple` and `complex`. Used
     when confidence ≥ 0.7.
   - Von unsure → stay on the local model already answering.
5. **Keyword rules** (`_HARD`: code, logic, maths, long messages, an attached
   file → `complex`; everything else → `simple`).

Von runs on the CPU in a thread with an 8-second limit. If it is missing,
loading, slow or raises, the keyword rules decide. Routing never blocks a
chat. Each decision is one log line:
`Router: code -> opencode-space-bunny (von, 0.902)`.

`ollama/testing_local_model/` holds a routing test: a list of questions with
the route each should get, and a script that reports accuracy. It generates
nothing and does not need Ollama.

## 8. OpenCode, the cloud routes (`opencode.py`)

- At startup the backend runs its own `opencode serve`: on 127.0.0.1, a
  random port, a random password that exists only in this process, in an
  empty temporary folder, with data in `ollama/.opencode/` and auto-update
  off.
- One reply = one throwaway session: create a session, send the whole
  conversation as one prompt (with a preface saying "answer in text, you have
  no tools"), read the event stream, delete the session. Stop aborts it.
- Text arrives as `message.part.delta` events and is passed on as `thinking`
  and `delta` events, the same as a local reply.
- **Tools are locked.** Shell, edit, web and outside-folder access are set to
  "ask", and every request is answered "reject". OpenCode cannot run a
  command, change a file or fetch a URL on this machine. Each attempt is
  logged.
- **The agent is not customised.** The free tier refuses anything but
  OpenCode's built-in agent.
- **Privacy.** Attached files and images are never sent. A conversation that
  has had an attachment stays local in Auto.
- **Failure.** No progress for 120 s, an error, or OpenCode missing → the
  next model of the route, then the local heavy model. The reply says so.

## 9. Docs page (Word files)

`documents.html` opens on four tasks: Create a new document (`word.html`),
Fill in a template (`#fill`; a designed template goes to `#library`, "Fill in a
saved template"), Change a document (`#edit`) and Read invoices into a sheet
(`invoices.html`). Each task shows its numbered steps. "Write a new one"
(`/api/documents/create`) is no longer on the page: Create a new document does
the same with more choices; the endpoint stays, and `#create` leads there.

The rule behind every task: **the model never writes the Word file.** It
answers in JSON (which blank gets which value, which edit operations to run)
and our code applies that to a copy of the file. Local models only.

The page loads `GET /api/documents/config` (models, upload limit, keep hours).
Uploaded and generated files live in `backend/data/documents/<random id>/` as
numbered versions and are deleted after 24 hours.

### Fill a template (a file with blanks)

1. `POST /upload` → `analyze_word()` finds the blanks and returns them with a
   text preview. The file is stored as version 0.
2. The user adds source files (their text comes from `/api/files`) and
   instructions.
3. `POST /{job}/propose` → `DocumentService.propose()` asks the model which
   value goes in which blank; suggestions stream back. Each value is tagged
   by origin: from the files, from the instructions, or written by the model.
4. The user reviews and edits the values.
5. `POST /{job}/fill` → `fill_word()` writes them into a copy; stored as a
   new version with a download link.

### Change a document

`POST /{job}/edit` → the document's outline (numbered paragraphs and text
boxes) and the instruction go to the model (`plan()`), which returns a list
of operations from a fixed set. `edit_word()` applies them and stores a new
version. The page shows the summary and what was applied.

### Write a new one

`POST /create` → the model writes Markdown, streamed live (`write()`).
`from_markdown()` turns it into a Word file, on the letterhead and styles of
an uploaded file if one was given. A designed template is refused here
(HTTP 409): writing a new body would delete its layout.

### Template library (designed templates: a resume, a styled report)

1. **Add:** `POST /templates` stores the file in
   `backend/data/templates/<id>/` (kept, not deleted after a day).
2. **Prepare, once:** `POST /templates/{id}/prepare` → the model labels every
   line `fixed`, `field` or `group_item`. The page shows the labels as
   colours; the user corrects them by clicking.
3. **Save:** `PUT /templates/{id}/slotmap` → `build_slotmap()` turns the
   reviewed labels into the template's slot map.
4. **Fill, any number of times:** `POST /templates/{id}/extract` → the model
   moves the user's content into the slot map's shape. After review,
   `POST /templates/{id}/render` → `render_template()` writes it into a copy:
   repeated blocks are copied for more items and removed for fewer.

Also: `GET /{job}/download`, and `GET /{job}/preview.pdf`, which uses
LibreOffice (`soffice`) when installed and is skipped when not.

How the model is asked (`model/service.py`): `OllamaClient.chat_json()` sends
one non-streaming request with a JSON schema in `format`, so Ollama holds the
answer to that shape. Thinking models get `think: false`. At most
`documents.max_jobs` (2) requests run at once.

## 10. Invoices tab

Files in, a sheet out. Local models only, always. Nothing is stored on the
server: a file is read in memory, and the rows live in the browser until
downloaded.

### Flow

1. The page loads `GET /api/documents/invoices/config`: the local models
   (marked "reads pictures" or not), accepted file types, the columns, and
   the scoring weights.
2. The user adds files and presses read. Files are sent one at a time to
   `POST /read` (20 MB each). Stop aborts the current one.
3. `reader.py` (no model) turns the file into pages:
   - PDF page with real text → its text and a picture of the page;
   - PDF page with under 200 characters, or a photo → a picture only;
   - Word file → its text, and each large picture inside as a page.
4. `service.py` sends one page at a time to the model with `chat_json()`:
   the system prompt and JSON schema from `prompts.py`, temperature 0, at
   most 6000 answer tokens. The model returns the documents on the page and
   their lines. It never sees the file name.
5. `checks.py` (no model) cleans every value, checks it and scores it; pages
   of one invoice are merged into one row (`merge`).
6. Events stream back: `progress` (Reading page 3 of 7), `model`, `rows`,
   `notice`, `done` (pages, rows, seconds).
7. The user reviews the table: edit a cell, add or remove a line, remove a
   row. The page recomputes the confidence when a value changes.
8. `POST /export` → `export.py` returns `.xlsx` or `.csv`.

If Ollama stops part-way through a long file, the rows read so far are kept
and a notice says where it stopped. If the model turns out not to take
pictures, the reading goes on with the page's text.

### The sheet

28 fixed columns, Confidence last. One row for each line of an invoice; the
invoice's own fields (number, date, seller, buyer, GST numbers, total, taxes,
Confidence) stand on its first row. Tick boxes add: a Summary sheet, a Checks
sheet, a totals row, the invoice's details on every row, its totals on every
row.

### The checks

Nothing the model returns is trusted as is. Confidence is a score from
checks, not a probability:

- a value found in the file's own text, a GSTIN with a correct check digit,
  or a total matching the amount in words → 1.0;
- a value read from a picture with nothing to confirm it → 0.8;
- a failed check, or a value the model marked unclear → 0.35.

Other rules: dates are read day first and written as `31-Mar-2026`; a due
date is never worked out from payment terms; a PAN is not put in a GST
column; taxable value + tax must equal the total within a rupee;
`TotalTaxAmount` is added up here, not asked of the model; lines that add up
confirm values, lines that do not are noted once and mark no cell; text is
written as text, never as a formula.

### From a terminal

`cd backend && python ../documents/read_invoices.py /path/to/folder --out out.xlsx`
reads a whole folder with the same code. `--model gemma4:12b` compares models.

## 10b. Word document tab

Several source files in, one designed document out. Local models only, always.

1. The page loads `GET /api/documents/word/config`: the local models, the
   seven kinds of document, the accepted file types, and whether a PDF can be
   made (is LibreOffice installed).
2. Each added file goes to `POST /source` and comes back as text
   (`sources.py`). An Excel sheet becomes a Markdown table. Pictures and old
   `.doc`/`.xls` files are refused with a reason.
3. `POST /create` sends the instructions, the sources and the page's choices.
   `WordService.write()` gives every source an equal share of the context,
   and the model writes the document as Markdown, streamed live into an
   editable box.
4. `build.py` puts the page's choices on top (kind, colour, cover, contents
   page, author, letterhead) and `renderer.py` writes the Word file: cover,
   numbered headings, styled tables, callout boxes, checklists, signatures.
5. The result is stored as a normal document job. **Download Word** uses
   `/api/documents/{job}/download`; **Download PDF** uses
   `/api/documents/word/{job}/pdf`, which converts with LibreOffice. With
   LibreOffice the page shows the PDF itself; without it, the file's text.
6. **Build again from this text** (`POST /build`) remakes the file from the
   edited text or with other choices, without the model. **Change something**
   uses the existing `/api/documents/{job}/edit`.

`renderer.py` comes from the Open WebUI tool "Generate Documents" (MIT) with
its Open WebUI and network parts removed. Settings: the `documents:` block.

## 11. All endpoints

| Method | Path | What it does |
| ------ | ---- | ------------ |
| GET | `/api/health` | Which Ollama hosts answer, and their versions |
| GET | `/api/models` | The picker list and the default |
| GET | `/api/theme` | Today's theme prompt |
| POST | `/api/files` | A PDF/DOCX/text file → its text |
| POST | `/api/chat` | A reply, streamed |
| GET | `/api/documents/config` | Models and limits for the Docs page |
| POST | `/api/documents/upload` | Store a `.docx`/`.dotx`, return its blanks |
| POST | `/api/documents/{job}/propose` | Suggested values, streamed |
| POST | `/api/documents/{job}/fill` | Write reviewed values into the file |
| POST | `/api/documents/{job}/edit` | Change a document by instruction, streamed |
| POST | `/api/documents/create` | Write a new document, streamed |
| GET | `/api/documents/{job}/download` | The Word file |
| GET | `/api/documents/{job}/preview.pdf` | A PDF preview (needs LibreOffice) |
| GET, POST | `/api/documents/templates` | List the library, add a template |
| GET, DELETE | `/api/documents/templates/{id}` | One template, or remove it |
| POST | `/api/documents/templates/{id}/prepare` | Label its lines, streamed |
| PUT | `/api/documents/templates/{id}/slotmap` | Save reviewed labels or rename |
| POST | `/api/documents/templates/{id}/extract` | Content → the template's shape, streamed |
| POST | `/api/documents/templates/{id}/render` | Fill the template |
| GET | `/api/documents/word/config` | Models, kinds of document, whether a PDF can be made |
| POST | `/api/documents/word/source` | A source file → its text |
| POST | `/api/documents/word/create` | Write and build a document, streamed |
| POST | `/api/documents/word/build` | Build again from the text, no model |
| GET | `/api/documents/word/{job}/pdf` | The document as a PDF |
| GET | `/api/documents/invoices/config` | Models, columns, scoring |
| POST | `/api/documents/invoices/read` | Read one invoice file, streamed |
| POST | `/api/documents/invoices/export` | Rows → `.xlsx` or `.csv` |
| GET, POST | `/api/sql/connections` | List saved databases; save one (tested first) |
| POST | `/api/sql/connections/test` | Connect and run `SELECT 1`, without saving |
| DELETE | `/api/sql/connections/{id}` | Remove a saved database |
| GET | `/api/sql/connections/{id}/tables` | Its tables and views |
| GET, POST | `/api/workspaces` | List workspaces and the search state; make one |
| GET, POST, DELETE | `/api/workspaces/{id}` | One workspace with its documents; rename / instructions; delete |
| POST | `/api/workspaces/{id}/documents` | Add one file, streamed (read, split, embed) |
| DELETE | `/api/workspaces/{id}/documents/{doc}` | Remove a document |
| POST | `/api/workspaces/{id}/reindex` | Give every passage a vector, streamed |
| POST | `/api/workspaces/{id}/search` | The passages a question would be answered from |
| GET | `/api/skills` | Which agent skills are on (open, read-only) |
| POST | `/api/admin/login` | Check the admin password (`X-Admin-Password`) |
| GET, POST | `/api/admin/skills` | The switches, and the model that runs skills; change them |
| GET, POST | `/api/admin/memories` | A workspace's memories; add one |
| POST, DELETE | `/api/admin/memories/{id}` | Edit or remove a memory |

Every streamed endpoint sends `application/x-ndjson` with
`X-Accel-Buffering: no`, so a proxy in front does not hold the text back.

## 12. What is stored, and where

| What | Where | How long |
| ---- | ----- | -------- |
| Chat messages | The browser tab only | Until the page is reloaded |
| Picked model | Browser `localStorage` | Kept |
| Chat attachments | Not stored; text is extracted and returned | — |
| Docs uploads and results | `backend/data/documents/` | 24 hours |
| Saved templates | `backend/data/templates/` | Kept |
| Word tab sources | Not stored; text is extracted and returned | — |
| Word tab results | `backend/data/documents/` | 24 hours |
| Invoice files and rows | Not stored | — |
| Saved databases (with passwords) | `backend/data/sql/connections.json`, mode 600 | Kept |
| Workspaces: settings, passages, vectors | `backend/data/workspaces/<id>/` | Until the workspace is deleted |
| Skill switches and memories | `backend/data/admin/` | Kept |
| Picked workspace and database | Browser `localStorage` | Kept |
| OpenCode's own data | `ollama/.opencode/` | Safe to delete |
| Von's weights | Hugging Face cache (`HF_HOME`) | Kept |

None of these folders are in git.

## 13. When something fails

| Situation | What happens |
| --------- | ------------ |
| Ollama is not running | "Can't reach Ollama at … Is `ollama serve` running?" (HTTP 503). No crash. |
| A model is not pulled | With Auto, another routed model answers. Picked directly: "… is not pulled. Run `ollama pull …`". |
| Von is missing, slow or fails | Keyword rules route the message. |
| OpenCode is missing or a cloud model fails | The next model in the route, then the local heavy model. The reply says so. |
| A conversation is larger than `num_ctx` | A warning in the reply; the start is cut. |
| A scanned PDF in chat | "… has no selectable text (a scanned PDF?)". Send a screenshot to the vision model instead. |
| A Python package for invoices is missing | The app still starts; the tab names the package. |
| A llama-server is down | "Can't reach llama-server at … Is `llama-server` running?" (HTTP 503). |
| A model is asked to call tools but llama-server lacks `--jinja` | The reply says to start it with `--jinja`. |
| The embedding model is not running | Workspaces search by words only; the Workspaces page says so; Re-index later. |
| A query that could write | Refused before it reaches the database; the model reads why and tries again. |
| No local model can call tools | A chat with a database, chart or memory request says so (HTTP 404). |
| `ADMIN_PASSWORD` is not set | The Admin panel is closed (HTTP 403); skills keep their last switches. |

## 13b. Added on the `tools` branch

**llama.cpp.** A host written `{url: ..., kind: llamacpp}` in `models.yaml`
is a llama-server. `registry.py` gives it a `LlamaCppClient` (`llamacpp.py`),
which talks to `/v1/chat/completions` and `/v1/embeddings` and hands back the
same chunks as Ollama: text, thinking (`reasoning_content`), tool calls (which
llama-server sends in pieces, put back together here) and timings. Nothing
above it knows which server answered. `ollama/config.llamacpp/` is a ready
config for Gemma 4 12B and Llama 3.1 8B.

**Tools in the chat.** `chat_stream()` takes any `tools` and a `run_tool`
function. When the model asks for a tool, it runs, its answer goes back as a
`tool` message, and the model is asked again, at most `sql.max_rounds` times;
the last round has no tools, so it must answer. `/api/chat` decides which
tools a message gets (`agent_skills.Toolbox`): the SQL connector's four when a
database is picked, `create_chart` when a chart is asked for or a database /
workspace is picked, `save_memory` / `forget_memory` when the message says
remember or forget. Any tool means a local model that can call them
(`select_tool_model`, preferring `sql.model`).

**SQL connector** (`sql/sql_connector/`). Saved connections; the tools
`list_databases`, `list_tables`, `get_table_schema`, `query`. A query must
pass `safety.check` (one read statement, read as that database reads its
strings, no comments), and runs on a connection that is read-only (SQLite
`mode=ro`; a read-only transaction and session elsewhere), runs one statement
only (prepared statements on PostgreSQL), has a time limit and returns at
most `sql.max_rows` rows.

**Workspaces** (`workspaces/workspace_engine/`). Files are split into
overlapping passages (a PDF page by page), stored in one SQLite file per
workspace with a full-text index, and given vectors by the embedding model
when it runs. A question is ranked by words and by meaning, the two lists are
merged, and the best `top_k` passages go to a local model with numbers to
cite; a `sources` event lists them under the answer.

**Agent skills and the Admin panel** (`skills/agent_skills/`,
`backend/app/admin/`, `admin.html`). Switches for Database queries, Charts,
Memory and "save and forget from the chat"; memories per workspace and a
general list, added to the system message of every local reply. The panel
needs `ADMIN_PASSWORD`.

**Documents page.** Opens on four tasks instead of six tabs; each task shows
its numbered steps.

## 14. Not built

No login (the Admin panel has one shared password), chat history only in the browser (not on the server), no text from scanned PDFs in chat, Figma
links are text only, no paid cloud models. What has not been tested on real
hardware is listed in `AGENTS.md` under "Not done".
