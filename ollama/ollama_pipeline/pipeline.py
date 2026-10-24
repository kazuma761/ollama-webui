"""The chat pipeline: request in, stream of events out.

This is the place to add steps later (retrieval, tools, routing, logging):
everything between the backend receiving a chat request and Ollama being
called goes through `chat_stream`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import date
from typing import Any

from .config import Theme
from .documents import MAX_FILE_CHARS
from .context import budget_for, fit, shrink
from .errors import ContextOverflow, ModelNotFound, PipelineError
from .opencode import PROVIDER as OPENCODE, OpenCodeError
from .registry import ModelEntry, Registry
from .router import AUTO_ID, Router

# Runs one tool call: (name, arguments) -> (text the model reads, a summary for the page).
RunTool = Callable[[str, Any], Awaitable[tuple[str, dict[str, Any]]]]



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


async def select_tool_model(
    registry: Registry, model_id: str | None, preferred: str = "", source: str = "database"
) -> tuple[ModelEntry, dict[str, Any]]:
    """The local model for a chat that uses tools, and a `route` event saying so.

    The picked model when it is local and can call tools; otherwise `preferred`
    (an id from models.yaml), else the first local model that can. Never a
    cloud model: what the tools return stays on this machine. `source` goes
    into the route event: "database", or "skills" for charts and memory.
    """
    note = None
    if model_id not in (AUTO_ID, None):
        picked = await registry.resolve(model_id)
        if picked.provider != OPENCODE and picked.tools is not False:
            return picked, {"type": "route", "model": picked.id, "label": picked.label, "route": None,
                            "source": "picked", "confidence": None}
        why = "runs in the cloud" if picked.provider == OPENCODE else "cannot call tools"
        note = f"{picked.label} {why}, so a local model that can use tools answers."

    entries = [e for e in await registry.list_models() if e.available and e.provider != OPENCODE]
    chosen = next((e for e in entries if e.id == preferred and e.tools is not False), None)
    chosen = chosen or next((e for e in entries if e.tools), None)
    if chosen is None:
        if not entries:
            await registry.resolve(None)  # raises the usual clear error: Ollama down, or nothing pulled
        raise ModelNotFound(
            "None of the local models here can call tools, which this needs (a database, a chart or memory). "
            "Pull one that can, e.g. `ollama pull qwen3:14b`."
        )
    event = {"type": "route", "model": chosen.id, "label": chosen.label, "route": None,
             "source": source, "confidence": None}
    if note:
        event["note"] = note
    return chosen, event


async def select_local_model(
    registry: Registry, router: Router, model_id: str | None, messages: list[dict[str, Any]]
) -> tuple[ModelEntry, dict[str, Any] | None]:
    """`select_model`, but never a cloud model: for replies whose context must stay on this machine."""
    entry, route, fallbacks = await select_model(registry, router, model_id, messages)
    if entry.provider != OPENCODE:
        return entry, route
    if model_id not in (AUTO_ID, None):
        raise PipelineError(f"{entry.label} runs in the cloud, and workspace documents and attached files stay on this machine. "
                            "Pick a local model or Auto.")
    local = next((e for e in fallbacks if e.provider != OPENCODE), None)
    if local is None:
        heavy = router.config.route(router.config.heavy_route)
        local = await registry.resolve(heavy.model if heavy else None)
    return local, {
        "type": "route", "model": local.id, "label": local.label, "route": None, "source": "fallback",
        "confidence": None, "note": "Documents and attached files stay on this machine, so a local model answers.",
    }


async def chat_stream(
    registry: Registry,
    entry: ModelEntry,
    messages: list[dict[str, Any]],
    options: dict[str, Any] | None = None,
    fallbacks: list[ModelEntry] | None = None,
    *,
    tools: list[dict[str, Any]] | None = None,
    run_tool: RunTool | None = None,
    max_rounds: int = 8,
) -> AsyncIterator[dict[str, Any]]:
    """Yields {"type": "route" | "notice" | "thinking" | "delta" | "tool_call" | "tool_result" | "done", ...} events.

    With `tools` and `run_tool` the model may call those tools: each call is
    run, its result goes back to the model, and the model is asked again,
    until it answers without calling one (at most `max_rounds` times). The
    caller decides what the tools are; this module knows none of them.
    """
    fallbacks = list(fallbacks or [])
    if tools and entry.provider == OPENCODE:
        raise PipelineError(f"{entry.label} runs in the cloud and cannot use this chat's tools. Pick a local model.")
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
    # Every request is made to fit what the model can read at once (see context.py):
    # the oldest turns go first, and room is kept for the answer.
    budget = budget_for(entry.context or merged.get("num_ctx"), merged)
    turn = max((i for i, m in enumerate(payload) if m["role"] == "user"), default=0)
    noticed = False

    client = registry.client(entry.host)
    offered = tools if tools and run_tool else None
    rounds = 0
    while True:
        text, calls, last = "", [], {}
        for attempt in range(3):
            sent, note = payload, None
            if budget:
                fitted = fit(payload, budget.prompt, keep_from=turn)
                sent, note = fitted.messages, fitted.notice(budget)
            received = False
            try:
                async for chunk in client.chat_stream(entry.model, sent, merged, tools=offered):
                    if not received and note and not noticed:  # said once, for the request the server took
                        noticed = True
                        yield {"type": "notice", "message": note}
                    received = True
                    message = chunk.get("message") or {}
                    if message.get("thinking"):
                        yield {"type": "thinking", "content": message["thinking"]}
                    if message.get("content"):
                        text += message["content"]
                        yield {"type": "delta", "content": message["content"]}
                    calls += message.get("tool_calls") or []
                    if chunk.get("done"):
                        last = chunk
                break
            except ContextOverflow as exc:
                # The estimate was short of the server's count: fit again with its numbers.
                if received or attempt == 2:
                    raise PipelineError(
                        f"This is more than {entry.label} can read at once even after shortening it "
                        f"({exc}). Start a new chat, or ask about a smaller part."
                    ) from exc
                budget = shrink(budget, sent, exc.prompt_tokens, exc.context)
        if not calls or not offered:
            yield {"type": "done", "model": entry.id, "stats": _stats(last)}
            return

        # The model asked for tools: run them, give it the results, and ask again.
        payload.append({"role": "assistant", "content": text, "tool_calls": calls})
        for call in calls:
            function = call.get("function") or {}
            name, args = function.get("name") or "", function.get("arguments") or {}
            yield {"type": "tool_call", "name": name, "args": args}
            result, summary = await run_tool(name, args)
            yield {"type": "tool_result", **summary, "name": name}
            payload.append({"role": "tool", "content": result, "tool_name": name,
                            **({"tool_call_id": call["id"]} if call.get("id") else {})})
        if text:
            yield {"type": "delta", "content": "\n\n"}
        rounds += 1
        if rounds >= max_rounds:
            # Enough lookups: the last turn gets no tools, so the model answers with what it has.
            offered = None
            payload.append({"role": "user", "content": "Stop calling tools now and answer with what you found."})


def todays_theme(themes: list[Theme], today: date | None = None) -> Theme | None:
    if not themes:
        return None
    return themes[(today or date.today()).toordinal() % len(themes)]
