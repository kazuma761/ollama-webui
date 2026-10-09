# AGENTS.md

Instructions for an AI coding agent working on this repo on a machine it has
not seen before. Read this first, then `PROJECT_FLOW.txt` for the code map.

## What this is

A local chat web app on top of Ollama. A user picks a model (or "Auto"), sends
a prompt with optional PDF/DOCX/text/image attachments, and the reply streams
back. With "Auto", a router sends each message to a light model, a reasoning
model or a vision model on this machine, or, for questions about software, to
OpenCode's free cloud models.

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
2. **Ollama** running, with the models this machine will use pulled
   (`ollama list` shows them). The Word documents page needs `qwen3:14b`.
3. **OpenCode 1.18.34, exactly that version:**
   `npm install -g opencode-ai@1.18.34`, then `opencode --version` must print
   `1.18.34`. Not the package `@opencode/cli`: that is 2.x and does not work.
4. **Models: one file, `ollama/config/models.yaml`.** The header at its top
   names the three entries to change. Nothing else in the project names a
   model.
5. **Check, then run.** `cd backend && uv run --inexact python -m ollama_pipeline`
   lists every model as ready or not. Then `./run.sh` (Windows:
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
library (result: a downloadable `.docx`), and Invoices (result: a sheet, see
"Invoices tab" below).

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
  read_invoices.py                 the same reading from a terminal, for a folder of files
backend/app/documents/             the HTTP side
  routes.py                          /api/documents/... endpoints
  storage.py                         uploads, versions, the template library on disk
  schemas.py                         request bodies
  invoices.py                        /api/documents/invoices/... endpoints (stores nothing)
frontend/documents.html            the page
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

1. Get the app running against real Ollama (Setup below).
2. Run the checks in "What to verify" and note pass/fail for each.
3. Fix what fails, keeping to "Rules".
4. Report back: hardware, models pulled, what passed, what failed, what you
   changed, and anything you could not test.

## Setup

Requirements: Python 3.11+ (3.12+ for Von), [uv](https://docs.astral.sh/uv/),
[Ollama](https://ollama.com/download) running, and for the cloud routes
Node.js with OpenCode 1.18.34. Ask the user before installing Ollama or
pulling models — models are several GB each.

```bash
cd backend && uv sync --extra router    # Python packages, with the Von router
npm install -g opencode-ai@1.18.34      # OpenCode, the tested version
ollama pull llama3.2:3b      # "simple" route
ollama pull qwen3:14b        # "complex" route (~9 GB)
ollama pull gemma3:4b        # screenshots (optional)
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
`backend/.env` (Von's weights). Ollama keeps models where `OLLAMA_MODELS` points.

Check hosts and which configured models are pulled:

```bash
cd backend && uv run --inexact python -m ollama_pipeline
```

### Models: one file

`ollama/config/models.yaml` is the only place that names models: the Ollama
tags, the OpenCode ids, and which route uses which. To use other local models,
change the `model:` tag and the `label:` of the `general`, `reasoning` and
`vision` entries (the file's header says the same) and restart. Any other
model pulled in Ollama appears in the picker by itself (`discover: true`).
`python -m ollama_pipeline` shows what is ready.

### Fit the models to this machine's hardware

Check GPU memory first (`nvidia-smi`, or system info on a Mac).
`ollama/config/` targets the production server: edit it there. On any other
machine copy that folder to `ollama/config.local/` and edit the copy: it is
ignored by git and used automatically when present. The server log and
`python -m ollama_pipeline` print which folder is in use. In `models.yaml`:

- If `qwen3:14b` does not fit, point the `reasoning` entry at a smaller tag
  (e.g. `qwen3:8b`, or `qwen3:4b` on a 4 GB GPU) and pull that instead.
- `default_options.num_ctx` is 16384. If replies are very slow or `ollama ps`
  shows a model partly on CPU, lower it (8192). Do not go below 8192 unless
  necessary: attached documents get cut off.
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
documents/read_invoices.py       invoices from a terminal
backend/app/documents/           its HTTP routes and storage
frontend/documents.html          its page
frontend/invoices.html           the Invoices tab
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

No login, no saved chat history, no text from scanned PDFs, Figma links are
passed as text only, no paid cloud models (e.g. Claude) - only OpenCode's
free tier.
