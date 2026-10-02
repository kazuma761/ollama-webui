"""The chat pipeline: request in, stream of events out.

This is the place to add steps later (retrieval, tools, routing, logging):
everything between the backend receiving a chat request and Ollama being
called goes through `chat_stream`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date
from typing import Any

from .config import Theme
from .errors import PipelineError
from .registry import ModelEntry, Registry

MAX_FILE_CHARS = 200_000


def build_messages(system: str, messages: list[dict[str, Any]], *, keep_images: bool = True) -> list[dict[str, Any]]:
    """Converts frontend messages (text + files + images) into Ollama messages."""
    out: list[dict[str, Any]] = []
    if system and not any(m["role"] == "system" for m in messages):
        out.append({"role": "system", "content": system})
    for message in messages:
        parts = [
            f'<file name="{f["name"]}">\n{f["content"][:MAX_FILE_CHARS]}\n</file>' for f in message.get("files") or []
        ]
        if message.get("content"):
            parts.append(message["content"])
        item: dict[str, Any] = {"role": message["role"], "content": "\n\n".join(parts)}
        if keep_images and message.get("images"):
            item["images"] = message["images"]
        out.append(item)
    return out


def _stats(chunk: dict[str, Any]) -> dict[str, Any]:
    tokens, nanos = chunk.get("eval_count"), chunk.get("eval_duration")
    return {
        "prompt_tokens": chunk.get("prompt_eval_count"),
        "output_tokens": tokens,
        "tokens_per_second": round(tokens / (nanos / 1e9), 1) if tokens and nanos else None,
        "total_seconds": round(chunk["total_duration"] / 1e9, 2) if chunk.get("total_duration") else None,
    }


async def chat_stream(
    registry: Registry,
    entry: ModelEntry,
    messages: list[dict[str, Any]],
    options: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yields {"type": "thinking" | "delta" | "done", ...} events for one reply."""
    can_see = entry.vision is not False
    if not can_see and messages and messages[-1].get("images"):
        raise PipelineError(f"{entry.label} can't read images. Pick a vision model to use screenshots.")

    payload = build_messages(entry.system, messages, keep_images=can_see)
    merged = {**entry.options, **(options or {})}

    async for chunk in registry.client(entry.host).chat_stream(entry.model, payload, merged):
        message = chunk.get("message") or {}
        if message.get("thinking"):
            yield {"type": "thinking", "content": message["thinking"]}
        if message.get("content"):
            yield {"type": "delta", "content": message["content"]}
        if chunk.get("done"):
            yield {"type": "done", "model": entry.id, "stats": _stats(chunk)}


def todays_theme(themes: list[Theme], today: date | None = None) -> Theme | None:
    if not themes:
        return None
    return themes[(today or date.today()).toordinal() % len(themes)]
