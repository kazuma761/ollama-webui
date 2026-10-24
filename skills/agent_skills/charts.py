"""The chart skill: the model hands over numbers, the page draws them. No model here.

AnythingLLM's `rechart` skill does the same: the model calls one tool with the
chart's kind and data, and the chat draws it. Here the data is checked first,
so a chart never shows numbers the model did not give, and a call with a
mistake in it comes back with the reason, for the model to correct.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

KINDS = ("bar", "line", "area", "pie")
MAX_LABELS = 60
MAX_SERIES = 6
MAX_SLICES = 12
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?|-?\.\d+")

DEFINITION = {
    "type": "function",
    "function": {
        "name": "create_chart",
        "description": (
            "Show the user a chart of numbers you already have: from a query, a document or the conversation. "
            "Use it when the user asks for a chart, graph or plot, or when a comparison of many numbers is clearer "
            "as a picture. Copy the numbers exactly; never invent or round them."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": list(KINDS),
                         "description": "bar to compare categories, line or area for a change over time, pie for the parts of one whole"},
                "title": {"type": "string", "description": "What the chart shows, e.g. Sales by city, 2026"},
                "labels": {"type": "array", "items": {"type": "string"},
                           "description": "The categories or dates along the bottom, in order"},
                "series": {
                    "type": "array",
                    "description": "One or more rows of numbers, each with one number per label",
                    "items": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}, "values": {"type": "array", "items": {"type": "number"}}},
                        "required": ["name", "values"],
                    },
                },
                "x_label": {"type": "string", "description": "Optional: what the labels are, e.g. Month"},
                "y_label": {"type": "string", "description": "Optional: what the numbers are, e.g. Revenue (INR)"},
            },
            "required": ["type", "title", "labels", "series"],
        },
    },
}

PROMPT = (
    "You can draw a chart with the create_chart tool: when the user asks for a chart or graph, or when many numbers "
    "compare better as a picture. Give it the numbers exactly as you have them."
)


class ChartError(ValueError):
    pass


def number(value: Any) -> float:
    """A number from what a model sends: 1200, "1,200", "₹1,200.50", "12%"."""
    if isinstance(value, bool):
        raise ChartError("A value is true/false, not a number.")
    if isinstance(value, (int, float)):
        result = float(value)
    else:
        found = _NUMBER.search(str(value).replace(",", ""))
        if not found:
            raise ChartError(f"'{value}' is not a number.")
        result = float(found.group(0))
    if not math.isfinite(result):
        raise ChartError(f"'{value}' is not a finite number.")
    return int(result) if result.is_integer() and abs(result) < 2**53 else result


def _loads(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def build(args: Any) -> dict[str, Any]:
    """Checks a create_chart call and returns the chart for the page. Raises ChartError with the reason."""
    args = _loads(args)
    if not isinstance(args, dict):
        raise ChartError("The arguments must be an object with type, title, labels and series.")
    kind = str(args.get("type") or args.get("chart_type") or "bar").lower().strip()
    if kind not in KINDS:
        raise ChartError(f"type must be one of {', '.join(KINDS)}.")
    title = str(args.get("title") or "").strip()[:120]

    labels = _loads(args.get("labels"))
    series = _loads(args.get("series"))
    data = _loads(args.get("data"))
    # Small models often send [{"label": "Pune", "value": 12}, ...] instead: take that too.
    if (not labels or not series) and isinstance(data, list) and data and isinstance(data[0], dict):
        keys = list(data[0])
        name_key = next((k for k in keys if not isinstance(data[0][k], (int, float))), keys[0])
        value_keys = [k for k in keys if k != name_key]
        labels = [str(row.get(name_key, "")) for row in data]
        series = [{"name": k, "values": [row.get(k) for row in data]} for k in value_keys]
    if isinstance(series, dict):
        series = [{"name": name, "values": values} for name, values in series.items()]
    if not series and isinstance(args.get("values"), list):
        series = [{"name": title or "Value", "values": args["values"]}]

    if not isinstance(labels, list) or not labels:
        raise ChartError("labels must be a non-empty list of the categories or dates.")
    if len(labels) > MAX_LABELS:
        raise ChartError(f"A chart can show at most {MAX_LABELS} labels; group or filter the data first.")
    if not isinstance(series, list) or not series:
        raise ChartError("series must be a list of {name, values}.")
    if len(series) > MAX_SERIES:
        raise ChartError(f"A chart can show at most {MAX_SERIES} series.")

    clean = []
    for i, item in enumerate(series, 1):
        if not isinstance(item, dict) or not isinstance(item.get("values"), list):
            raise ChartError(f"Series {i} must be {{name, values}} with a list of numbers.")
        values = [number(v) for v in item["values"]]
        if len(values) != len(labels):
            raise ChartError(f"Series {i} has {len(values)} values for {len(labels)} labels; give one value per label.")
        clean.append({"name": str(item.get("name") or f"Series {i}")[:60], "values": values})

    if kind == "pie":
        if len(clean) != 1:
            raise ChartError("A pie chart takes exactly one series.")
        if len(labels) > MAX_SLICES:
            raise ChartError(f"A pie chart can have at most {MAX_SLICES} slices; use a bar chart for more.")
        if any(v < 0 for v in clean[0]["values"]):
            raise ChartError("A pie chart cannot show negative values; use a bar chart.")
    return {
        "type": kind, "title": title, "labels": [str(label)[:60] for label in labels], "series": clean,
        "x_label": str(args.get("x_label") or "")[:60], "y_label": str(args.get("y_label") or "")[:60],
    }


class ChartTool:
    definitions = [DEFINITION]

    def system_prompt(self) -> str:
        return PROMPT

    async def run(self, name: str, args: Any) -> tuple[str, dict[str, Any]]:
        try:
            chart = build(args)
        except ChartError as exc:
            return f"Error: {exc} Fix the call and try again.", {"error": str(exc)}
        points = len(chart["labels"]) * len(chart["series"])
        return (
            f"The {chart['type']} chart \"{chart['title']}\" is now shown to the user, above your answer. "
            "Do not repeat all of its numbers; point out what matters in them.",
            {"chart": chart, "rows": points},
        )
