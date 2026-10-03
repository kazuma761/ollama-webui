# ollama-webui

A chat interface for local Ollama models: pick a model, type a prompt, get a streamed reply.

## Structure

```
├── ollama/      Ollama layer — hosts, model registry, chat pipeline
│   ├── config/models.yaml      models shown in the picker
│   ├── config/themes.yaml      "Today's Theme" prompts
│   └── ollama_pipeline/        Python package
├── backend/     FastAPI app — the API, also serves the frontend
│   └── app/
├── frontend/    index.html — the whole UI in one file
└── run.sh       starts everything
```

## Run

Requires [Ollama](https://ollama.com/download) and [uv](https://docs.astral.sh/uv/).

```bash
ollama pull llama3.2:3b
ollama pull qwen3:14b
./run.sh
```

Open <http://127.0.0.1:8000>.

With **Auto** selected, each message is routed to the light or the reasoning model. Optional smarter routing with the Von decision model: `cd backend && uv sync --extra router`. See `PROJECT_FLOW.txt` for how it all fits together.

Any model you pull shows up in the picker. To add labels, system prompts or another Ollama server, edit `ollama/config/models.yaml` and restart.
