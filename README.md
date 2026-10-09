# ollama-webui

A chat interface for local Ollama models: pick a model, type a prompt, get a streamed reply.

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

Requires [Ollama](https://ollama.com/download), Python 3.12+ with [uv](https://docs.astral.sh/uv/) (or pip), and for the cloud routes Node.js.

```bash
cd backend && uv sync --extra router && cd ..   # or: pip install -r requirements.txt
npm install -g opencode-ai@1.18.34              # OpenCode: this version, not 2.x
ollama pull llama3.2:3b
ollama pull qwen3:14b
./run.sh
```

To use other models, edit `ollama/config/models.yaml`: it is the only file that names models, and its header says which lines to change.

Open <http://127.0.0.1:8000>. On Windows use `powershell -ExecutionPolicy Bypass -File run.ps1` instead of `./run.sh`.

With **Auto** selected, each message is routed to the light or the reasoning model. Optional smarter routing with the Von decision model: `cd backend && uv sync --extra router`. See `PROJECT_FLOW.txt` for how it all fits together.

Optional cloud route: with [OpenCode](https://github.com/anomalyco/opencode) 1.x installed (`npm i -g opencode-ai@1`), Auto sends questions about software to its free models, trying the next one when a model does not answer. Those messages leave your machine; attached files never do.

Any model you pull shows up in the picker. To add labels, system prompts or another Ollama server, edit `ollama/config/models.yaml` and restart. For a model list that applies to one machine only, copy `ollama/config/` to `ollama/config.local/` and edit that: it is not in git and is used automatically.
