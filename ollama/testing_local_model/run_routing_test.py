"""Tests the auto-routing only: which model would answer each question.

Nothing is generated. No Ollama is needed either - the router decides from the question
alone. For every question in questions.yaml it asks the router what it would pick, compares
that with the route the question should have gone to, and writes everything to a JSON file.

Run (from the repo root; use the backend's environment so Von is available if installed):

    cd backend && uv run --inexact python ../ollama/testing_local_model/run_routing_test.py

Options:
    --config    models file with the routes (default: test_models.yaml next to this script)
    --questions questions file (default: questions.yaml)
    --out       where to write the JSON (default: results/routing_<time>.json)
    --classifier  which classifier to test (default: router; see CLASSIFIERS below)

The "router" classifier is the app's own ollama_pipeline.Router, so what is tested is what
the app does. Von is used if installed; if not, the keyword rules decide and the report
says so - those are not the numbers the server will give.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.append(str(HERE.parent))  # lets `ollama_pipeline` import without being installed

from ollama_pipeline import Router, load_config  # noqa: E402


# ── Classifiers ──────────────────────────────────────────────────────────────
# A classifier takes a question and returns a Prediction. To test another one later
# (a different model, a different library), write a class with the same two methods
# and add it to CLASSIFIERS. Nothing else in this file needs to change.

class Prediction(dict):
    """route, model (id from test_models.yaml), source, confidence."""


class RouterClassifier:
    """The app's own Router, as configured in test_models.yaml."""

    name = "router"

    def __init__(self, config):
        self.config = config
        self.router = Router(config.router)
        self.router._load_von()  # load now, so the first question is not decided by rules
        self.engine = "von" if self.router._von is not None else "rules (Von is not installed or failed to load)"

    async def predict(self, question: str) -> Prediction:
        decision = await self.router.decide([{"role": "user", "content": question}])
        return Prediction(
            route=decision.route, model=decision.model, source=decision.source, confidence=decision.confidence
        )


CLASSIFIERS = {"router": RouterClassifier}


# ── Test run ─────────────────────────────────────────────────────────────────

def load_test_config(models_file: Path):
    """load_config() reads a folder's models.yaml, so give it a temporary folder with this one."""
    with tempfile.TemporaryDirectory() as folder:
        shutil.copy(models_file, Path(folder) / "models.yaml")
        return load_config(Path(folder))


async def run(args) -> dict:
    config = load_test_config(Path(args.config))
    questions = yaml.safe_load(Path(args.questions).read_text(encoding="utf-8"))["questions"]
    labels = {m.id: m for m in config.models}
    routes = [r.name for r in config.router.routes]
    classifier = CLASSIFIERS[args.classifier](config)
    print(f"Classifier: {classifier.name} ({classifier.engine}); {len(questions)} questions; routes: {', '.join(routes)}")

    results = []
    for i, item in enumerate(questions, 1):
        started = time.perf_counter()
        prediction = await classifier.predict(item["q"])
        millis = round((time.perf_counter() - started) * 1000)
        expected_model = config.router.route(item["expect"]).model
        entry = labels.get(prediction["model"])
        results.append({
            "n": i,
            "question": item["q"],
            "category": item.get("category", ""),
            "expected_route": item["expect"],
            "expected_model": expected_model,
            "predicted_route": prediction["route"],
            "predicted_model": prediction["model"],
            "predicted_model_tag": entry.model if entry else None,
            "source": prediction["source"],
            "confidence": prediction["confidence"],
            "correct": prediction["route"] == item["expect"],
            "ms": millis,
        })
        mark = "ok  " if results[-1]["correct"] else "MISS"
        print(f"{mark} {i:>2}. {item['expect']:<8} -> {str(prediction['route']):<8} {prediction['source']:<6} "
              f"{prediction['confidence'] if prediction['confidence'] is not None else '-':<6} {item['q'][:60]}")

    return {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "classifier": classifier.name,
        "engine": classifier.engine,
        "config_file": str(Path(args.config).name),
        "summary": summarise(results, routes),
        "results": results,
    }


def summarise(results: list[dict], routes: list[str]) -> dict:
    correct = sum(r["correct"] for r in results)
    by_route = {}
    for route in routes:
        asked = [r for r in results if r["expected_route"] == route]
        by_route[route] = {
            "questions": len(asked),
            "correct": sum(r["correct"] for r in asked),
            "accuracy": round(sum(r["correct"] for r in asked) / len(asked), 3) if asked else None,
        }
    confusion = defaultdict(Counter)
    for r in results:
        confusion[r["expected_route"]][str(r["predicted_route"])] += 1
    return {
        "total": len(results),
        "correct": correct,
        "accuracy": round(correct / len(results), 3) if results else None,
        "by_source": dict(Counter(r["source"] for r in results)),
        "by_expected_route": by_route,
        "confusion (expected -> predicted)": {k: dict(v) for k, v in confusion.items()},
        "wrong": [
            {"question": r["question"], "expected": r["expected_route"], "got": r["predicted_route"]}
            for r in results if not r["correct"]
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(HERE / "test_models.yaml"))
    parser.add_argument("--questions", default=str(HERE / "questions.yaml"))
    parser.add_argument("--classifier", default="router", choices=sorted(CLASSIFIERS))
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    report = asyncio.run(run(args))
    out = Path(args.out) if args.out else HERE / "results" / f"routing_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    s = report["summary"]
    print(f"\nAccuracy: {s['correct']}/{s['total']} = {s['accuracy']:.1%}   (decided by: {s['by_source']})")
    for route, v in s["by_expected_route"].items():
        print(f"  {route:<8} {v['correct']}/{v['questions']}")
    print(f"Written to {out}")


if __name__ == "__main__":
    main()
