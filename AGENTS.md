# AGENTS.md

Instructions for an AI coding agent working on this repo on a machine it has
not seen before. Read this first, then `PROJECT_FLOW.txt` for the code map.

## What this is

A local chat web app on top of Ollama. A user picks a model (or "Auto"), sends
a prompt with optional PDF/DOCX/text/image attachments, and the reply streams
back. With "Auto", a router sends each message to a light model, a reasoning
model, or a vision model.

## Current status — read before trusting anything

- All of it was developed on a laptop **without Ollama installed** and tested
  only against a stand-in server that imitates Ollama's API.
- The Von routing model was **never installed or run**; its integration was
  written from Von's README and tested with a fake module.
- So your first job on this machine is to find out what actually works with
  real Ollama and real models. Expect small integration bugs. Fix them, and
  report what you changed.

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
`cd backend && uv run --inexact python -m app` instead.

Check hosts and which configured models are pulled:

```bash
cd backend && uv run --inexact python -m ollama_pipeline
```

### Fit the models to this machine's hardware

Check GPU memory first (`nvidia-smi`, or system info on a Mac). Then edit
`ollama/config/models.yaml`:

- If `qwen3:14b` does not fit, point the `reasoning` entry at a smaller tag
  (e.g. `qwen3:8b`) and pull that instead.
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
| 11 | With Von installed: repeat 1–3 | header tooltip says "Routed by von" with a confidence |

Also check `ollama ps` while testing: both routed models should stay loaded
and on the GPU. If Ollama unloads one each time the route changes, set
`OLLAMA_MAX_LOADED_MODELS=2` for the Ollama server.

Things most likely to need fixing with real Ollama:
- `qwen3` is a thinking model. Thinking arrives as separate `thinking` events
  (ignored by the UI, which shows dots until the answer starts). On older
  Ollama versions it may arrive inline as `<think>…</think>` text instead.
- Vision detection relies on `capabilities` from Ollama's `/api/show`.
- Von's behaviour: `von.decide(...)` return shape, `VON_DEVICE`, load time.

## Where things are

```
frontend/index.html              whole UI (one file)
backend/app/routes.py            API endpoints
ollama/config/models.yaml        hosts, models, routes, num_ctx  ← most changes go here
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
passed as text only, no routing to cloud models.
