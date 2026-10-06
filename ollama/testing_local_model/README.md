# Routing test (local models only)

Tests which model the auto-router would pick for a question. Nothing is generated and Ollama
does not need to be running. No OpenCode / cloud routes.

| File | What it is |
| --- | --- |
| `test_models.yaml` | the local models and routes under test (same format as `config/models.yaml`; nothing in the app reads it) |
| `questions.yaml` | the questions, each with the route it should go to |
| `run_routing_test.py` | the script |
| `results/` | one JSON per run (not in git) |

Run it with the backend's environment, so Von is used when installed:

```bash
cd backend && uv run --inexact python ../ollama/testing_local_model/run_routing_test.py
```

Each entry in the JSON has: the question, expected route/model, predicted route/model (and its
Ollama tag), `source` (`von` or `rules`), `confidence`, `correct`, and `ms`. The top has the
summary: accuracy, per-route counts, a confusion table and the list of wrong ones.

**Von matters.** Without it (`Router: von-sdk is not installed`) the keyword rules decide, and
they only know "simple" and "complex" - a `coding` route is never chosen. The report's `engine`
field says which one ran. For the server's numbers install it:
`cd backend && uv sync --extra router --inexact`.

Another classifier later: add a class with `async predict(question)` to `CLASSIFIERS` in the
script and run it with `--classifier <name>`.
