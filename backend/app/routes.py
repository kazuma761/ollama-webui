import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from ollama_pipeline import ModelNotFound, OllamaUnavailable, PipelineError, Registry, chat_stream, todays_theme

from .schemas import ChatRequest

router = APIRouter()


def get_registry(request: Request) -> Registry:
    return request.app.state.registry


@router.get("/health")
async def health(registry: Registry = Depends(get_registry)):
    hosts = await registry.health()
    return {"status": "ok" if any(h["ok"] for h in hosts.values()) else "degraded", "hosts": hosts}


@router.get("/models")
async def models(registry: Registry = Depends(get_registry)):
    entries = await registry.list_models()
    return {"default": registry.default_id(entries), "models": [e.public() for e in entries]}


@router.get("/theme")
async def theme(registry: Registry = Depends(get_registry)):
    today = todays_theme(registry.config.themes)
    if today is None:
        raise HTTPException(404, "No themes configured in ollama/config/themes.yaml.")
    return {"title": today.title, "prompt": today.prompt}


@router.post("/chat")
async def chat(body: ChatRequest, registry: Registry = Depends(get_registry)):
    """Streams the reply as newline-delimited JSON events: thinking, delta, done, error."""
    try:
        entry = await registry.resolve(body.model)
    except ModelNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except OllamaUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc

    messages = [m.model_dump() for m in body.messages]

    async def events() -> AsyncIterator[str]:
        try:
            async for event in chat_stream(registry, entry, messages, body.options):
                yield json.dumps(event) + "\n"
        except PipelineError as exc:
            yield json.dumps({"type": "error", "message": str(exc)}) + "\n"

    return StreamingResponse(
        events(),
        media_type="application/x-ndjson",
        # Keeps nginx and similar proxies from buffering the stream.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
