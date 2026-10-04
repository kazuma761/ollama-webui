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
from .documents import MAX_FILE_CHARS
from .errors import PipelineError
from .opencode import PROVIDER as OPENCODE, OpenCodeError
from .registry import ModelEntry, Registry
from .router import AUTO_ID, Router

# Rough characters-per-token figure, only used to warn about an overfull context.
CHARS_PER_TOKEN = 3.5


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


async def picker_models(registry: Registry, router: Router) -> tuple[str | None, list[dict[str, Any]]]:
    """Models for the frontend picker, with the Auto entry first when routing is on."""
    entries = await registry.list_models()
    models = [e.public() for e in entries]
    default = registry.default_id(entries)
    if router.enabled:
        available = {e.id for e in entries if e.available}
        routable = [m for m in router.models() if m in available]
        auto = ModelEntry(
            id=AUTO_ID,
            label="Auto",
            model="",
            host="",
            description="Picks the right model for each message",
            available=bool(routable),
            vision=router.config.image_model in available or None,
            configured=True,
        )
        models.insert(0, auto.public())
        if routable:
            default = AUTO_ID
    return default, models


async def select_model(
    registry: Registry, router: Router, model_id: str | None, messages: list[dict[str, Any]]
) -> tuple[ModelEntry, dict[str, Any] | None, list[ModelEntry]]:
    """Resolves the model for one reply.

    Returns the entry, a `route` event when routed, and - when routing picked a
    cloud model - the models to fall back on if it does not answer: the other
    cloud models of its route, in their order, then one local model.
    """
    if not router.enabled or model_id not in (AUTO_ID, None):
        return await registry.resolve(None if model_id == AUTO_ID else model_id), None, []

    decision = await router.decide(messages)
    entries = {e.id: e for e in await registry.list_models()}
    route = router.config.route(decision.route) if decision.route else None
    # The chosen model first, then the rest of its route, then any other routed model, local ones first.
    order = dict.fromkeys([decision.model, *(route.models if route else ()), *router.models()])
    usable = [entries[m] for m in order if m in entries and entries[m].available]
    if usable:
        entry, rest = usable[0], usable[1:]
        event = {"type": "route", "model": entry.id, "label": entry.label, **decision.event()}
        if entry.id != decision.model:
            event["source"] = "fallback"
        fallbacks = []
        if entry.provider == OPENCODE:
            fallbacks = [e for e in rest if e.provider == OPENCODE and route and e.id in route.models]
            fallbacks += [e for e in rest if e.provider != OPENCODE][:1]
        return entry, event, fallbacks

    # None of the routed models can run: use whatever can, or raise the usual clear error.
    entry = await registry.resolve(None)
    event = {"type": "route", "model": entry.id, "label": entry.label, **decision.event(), "source": "fallback"}
    return entry, event, []


def _context_notice(entry: ModelEntry, payload: list[dict[str, Any]], options: dict[str, Any]) -> str | None:
    limit = options.get("num_ctx")
    if not limit:
        return None
    estimate = int(sum(len(m["content"]) for m in payload) / CHARS_PER_TOKEN)
    if estimate <= limit * 0.9:
        return None
    return (
        f"This conversation is roughly {estimate:,} tokens but {entry.label} is set to read {limit:,}, "
        "so part of it will be cut off. Raise num_ctx in ollama/config/models.yaml or attach less."
    )


async def chat_stream(
    registry: Registry,
    entry: ModelEntry,
    messages: list[dict[str, Any]],
    options: dict[str, Any] | None = None,
    fallbacks: list[ModelEntry] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yields {"type": "route" | "notice" | "thinking" | "delta" | "done", ...} events for one reply."""
    fallbacks = list(fallbacks or [])
    if entry.provider == OPENCODE and (messages[-1].get("files") or messages[-1].get("images")):
        yield {"type": "notice", "message": f"Attached files are not sent to {entry.label}; it only sees your text."}
    while entry.provider == OPENCODE:
        answered = False
        try:
            # With another model lined up there is no point in waiting for this one to retry.
            async for event in registry.opencode.chat_stream(entry.model, messages, patient=not fallbacks):
                answered = answered or event["type"] == "delta"
                yield {**event, "model": entry.id} if event["type"] == "done" else event
            return
        except OpenCodeError as exc:
            # Nothing was shown yet and routing has another model to offer: answer there instead.
            if answered or not fallbacks:
                raise
            failed, entry = entry, fallbacks.pop(0)
            instead = f"{entry.label} answers instead" if entry.provider == OPENCODE else "answered locally instead"
            yield {"type": "route", "model": entry.id, "label": entry.label, "route": None,
                   "source": "fallback", "confidence": None}
            yield {"type": "notice", "message": f"{failed.label} could not answer ({exc}) - {instead}."}

    can_see = entry.vision is not False
    if not can_see and messages and messages[-1].get("images"):
        raise PipelineError(f"{entry.label} can't read images. Pick a vision model to use screenshots.")

    payload = build_messages(entry.system, messages, keep_images=can_see)
    merged = {**entry.options, **(options or {})}

    notice = _context_notice(entry, payload, merged)
    if notice:
        yield {"type": "notice", "message": notice}

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
