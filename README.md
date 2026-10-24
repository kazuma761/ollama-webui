# ollama-webui

A chat interface for local models served by llama.cpp's `llama-server` (Gemma 4 12B and Llama 3.1 8B on the server): pick a model, type a prompt, get a streamed reply. The `ollama/` folder name is historical; Ollama itself is not used.

## Structure

```
├── ollama/      Ollama layer — hosts, model registry, chat pipeline
│   ├── config/models.yaml      models shown in the picker
│   ├── config/themes.yaml      "Today's Theme" prompts
│   └── ollama_pipeline/        Python package
├── documents/   Word documents page — engine/ (works on .docx) and model/ (prompts, calls);
│                invoices/ reads invoices (PDF, Word, photos) into an Excel or CSV sheet
├── backend/     FastAPI app — the API, also serves the frontend
│   └── app/
├── frontend/    index.html — the whole UI in one file
└── run.sh       starts everything
```

## Run

Requires llama.cpp's `llama-server` with the two GGUF models, Python 3.12+ with [uv](https://docs.astral.sh/uv/) (or pip), and for the cloud routes Node.js.

```bash
cd backend && uv sync --extra router && cd ..   # or: pip install -r requirements.txt
npm install -g opencode-ai@1.18.34              # OpenCode: this version, not 2.x
cp -r ollama/config.llamacpp ollama/config.local
# start both llama-servers with the commands at the top of ollama/config.local/models.yaml
./run.sh
```

`ollama/config.local/models.yaml` is the only file that names models. AGENTS.md ("Where it runs") has the details.

Open <http://127.0.0.1:8000>. On Windows use `powershell -ExecutionPolicy Bypass -File run.ps1` instead of `./run.sh`.

With **Auto** selected, each message is routed to the light or the reasoning model. Optional smarter routing with the Von decision model: `cd backend && uv sync --extra router`. See `PROJECT_FLOW.txt` for how it all fits together.

Optional cloud route: with [OpenCode](https://github.com/anomalyco/opencode) 1.x installed (`npm i -g opencode-ai@1`), Auto sends questions about software to its free models, trying the next one when a model does not answer. Those messages leave your machine; attached files never do.

To change labels, system prompts or servers, edit `ollama/config.local/models.yaml` and restart. It is not in git and is used automatically when present.
