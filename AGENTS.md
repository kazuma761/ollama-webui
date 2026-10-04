# AGENTS.md

Instructions for an AI coding agent working on this repo on a machine it has
not seen before. Read this first, then `PROJECT_FLOW.txt` for the code map.

## What this is

A local chat web app on top of Ollama. A user picks a model (or "Auto"), sends
a prompt with optional PDF/DOCX/text/image attachments, and the reply streams
back. With "Auto", a router sends each message to a light model, a reasoning
model, or a vision model.

## Current status — read before trusting anything

- Developed on a laptop without Ollama, then run against real Ollama 0.32.5 on
  a second laptop: Windows 11, GTX 1650 (4 GB VRAM), 16 GB RAM, with
  `llama3.2:3b`, `gemma3:4b`, and `qwen3:4b` standing in for `qwen3:14b`.
  All eleven checks in "What to verify" pass there, with Von 1.3.7 loaded
  (10 by pointing the backend at a port with no Ollama, not by stopping it).
- **Not run anywhere yet:** `qwen3:14b`, a 16 GB GPU, and two routed models
  loaded at once. On 4 GB no routed model fits fully on the GPU at `num_ctx`
  8192 and Ollama swaps models on every route change, so nothing measured
  there says how fast the server will be.
- So on the server the open questions are about hardware: do the models fit,
  do both routed models stay loaded, and how long do replies take. Expect
  small integration bugs anyway. Fix them, and report what you changed.

## OpenCode route (cloud) — added after the Ollama test run

With Auto, the hardest engineering questions (architecture and system design,
hard-to-find bugs such as deadlocks, races and memory leaks, large refactors)
go to OpenCode's free cloud models instead of Ollama. Code:
`ollama/ollama_pipeline/opencode.py`; config: `opencode:` and the `expert`
route in `models.yaml`.

- Tested on macOS with OpenCode 1.18.34 and the real free model
  `opencode/big-pickle`. **Not tested on Windows or Linux.** On Windows check
  that the `opencode` shim starts from Python and that Stop kills it
  (`taskkill /T` in `_kill_tree`).
- It runs `opencode run --agent plan --format json` in an empty temp folder,
  with the prompt on stdin. Text arrives a block at a time, not word by word.
- **Do not customise the agent, its prompt, or which tools exist.** OpenCode's
  service answers "free tier can only be used from within OpenCode" (403) for
  anything but a built-in agent. Do not work around that by faking a client.
- Shell, edit and web tools are set to permission "ask"; headless mode rejects
  those, so OpenCode cannot run commands or touch files here. Keep it so.
- Privacy rules, keep them: attached files and images are never sent; in Auto
  a conversation that has had any attachment stays on local models.
- If OpenCode is missing, refuses, errors or times out, the local heavy model
  answers and the reply says so.
- If `ollama/config.local/models.yaml` exists on this machine it overrides
  `ollama/config/` — copy the new `opencode:` block, the `opencode` model and
  the `expert` route into it, or the route will not exist here.

Checks: (a) "design the architecture for a chat service" → header shows
"Auto · OpenCode Big Pickle"; (b) follow with "make it shorter" → stays;
(c) same question with a PDF attached → local reasoning model; (d) rename the
command in config to something that does not exist → local model answers.

## Your task on this machine

1. Get the app running against real Ollama (Setup below).
2. Run the checks in "What to verify" and note pass/fail for each.
3. Fix what fails, keeping to "Rules".
4. Report back: hardware, models pulled, what passed, what failed, what you
   changed, and anything you could not test.

## Setup

Requirements: Python 3.11+ (3.12+ for Von), [uv](https://docs.astral.sh/uv/),
[Ollama](https://ollama.com/download) running. Ask the user before installing
Ollama or pulling models — models are several GB each.

```bash
ollama pull llama3.2:3b      # "simple" route
ollama pull qwen3:14b        # "complex" route (~9 GB)
ollama pull gemma3:4b        # screenshots (optional)
./run.sh                     # http://127.0.0.1:8000
```

`run.sh` needs bash and `lsof` (macOS/Linux/WSL). On plain Windows run
`powershell -ExecutionPolicy Bypass -File run.ps1` instead.

Downloads go to the user's home folder by default. If that disk is short of
space, set `UV_CACHE_DIR` before `uv sync` (packages) and `HF_HOME` in
`backend/.env` (Von's weights). Ollama keeps models where `OLLAMA_MODELS` points.

Check hosts and which configured models are pulled:

```bash
cd backend && uv run --inexact python -m ollama_pipeline
```

### Fit the models to this machine's hardware

Check GPU memory first (`nvidia-smi`, or system info on a Mac).
`ollama/config/` targets the production server. On any other machine copy that
folder to `ollama/config.local/` and edit the copy: it is ignored by git and
used automatically when present. The server log and
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
| 2 | "write a python function for binary search and explain its complexity" | reasoning model answers |
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
  (ignored by the UI, which shows dots until the answer starts). On older
  Ollama versions it may arrive inline as `<think>…</think>` text instead.
  On slow hardware the dots can last a minute.
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
ollama/config/models.yaml        hosts, models, routes, num_ctx  ← most changes go here
ollama/config.local/             optional per-machine copy of config/, not in git
ollama/ollama_pipeline/
  router.py                      which model answers (Von + keyword rules)
  pipeline.py                    builds the prompt, streams the reply
  registry.py                    which models exist / are pulled
  documents.py                   PDF/DOCX/text → text
  client.py                      raw calls to Ollama
```

## Rules

- Calls go one way: `frontend → backend → ollama`. No Ollama-specific code in
  `backend/`; it only calls the `ollama_pipeline` package.
- Routing must never block a chat. Any router failure falls back to keyword
  rules; a missing routed model falls back to another one. Keep it that way.
- `frontend/index.html` follows a pixel-exact design. On desktop the composer
  toolbar uses absolute coordinates — do not convert it to flexbox or change
  those numbers.
- Backend or `ollama/` changes need a server restart. Frontend changes only
  need a browser refresh.
- New Python dependency: add it to the right `pyproject.toml`, then
  `cd backend && uv sync --inexact` (plain `uv sync` removes the Von extra).
- Do not commit `.env`, model weights or the user's documents.
- Work on a branch and open a pull request against `main`; do not push to
  `main` directly.

## Not built yet

No login, no saved chat history, no text from scanned PDFs, Figma links are
passed as text only, no paid cloud models (e.g. Claude) - only OpenCode's
free tier.
