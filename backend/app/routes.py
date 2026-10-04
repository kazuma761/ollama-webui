import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from ollama_pipeline import (
    ModelNotFound,
    OllamaUnavailable,
    PipelineError,
    Registry,
    Router,
    UnsupportedDocument,
    chat_stream,
    extract_text,
    picker_models,
    select_model,
    todays_theme,
)

from .schemas import ChatRequest

MAX_UPLOAD_BYTES = 20 * 1024 * 1024

router = APIRouter()


def get_registry(request: Request) -> Registry:
    return request.app.state.registry


def get_router(request: Request) -> Router:
    return request.app.state.router


@router.get("/health")
async def health(registry: Registry = Depends(get_registry)):
    hosts = await registry.health()
    return {"status": "ok" if any(h["ok"] for h in hosts.values()) else "degraded", "hosts": hosts}


@router.get("/models")
async def models(registry: Registry = Depends(get_registry), auto: Router = Depends(get_router)):
    default, entries = await picker_models(registry, auto)
    return {"default": default, "models": entries}


@router.get("/theme")
async def theme(registry: Registry = Depends(get_registry)):
    today = todays_theme(registry.config.themes)
    if today is None:
        raise HTTPException(404, "No themes configured in ollama/config/themes.yaml.")
    return {"title": today.title, "prompt": today.prompt}


@router.post("/files")
def extract_file(file: UploadFile):
    """Extracts the text of a PDF, DOCX or text file so it can be attached to a message."""
    name = file.filename or "file"
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"{name} is over 20 MB")
    try:
        content, truncated = extract_text(name, data)
    except UnsupportedDocument as exc:
        raise HTTPException(415, str(exc)) from exc
    return {"name": name, "content": content, "truncated": truncated}


@router.post("/chat")
async def chat(
    body: ChatRequest, registry: Registry = Depends(get_registry), auto: Router = Depends(get_router)
):
    """Streams the reply as newline-delimited JSON events: route, notice, thinking, delta, done, error."""
    messages = [m.model_dump() for m in body.messages]
    try:
        entry, route, fallback = await select_model(registry, auto, body.model, messages)
    except ModelNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except OllamaUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc

    async def events() -> AsyncIterator[str]:
        try:
            if route:
                yield json.dumps(route) + "\n"
            async for event in chat_stream(registry, entry, messages, body.options, fallback):
                yield json.dumps(event) + "\n"
        except PipelineError as exc:
            yield json.dumps({"type": "error", "message": str(exc)}) + "\n"

    return StreamingResponse(
        events(),
        media_type="application/x-ndjson",
        # Keeps nginx and similar proxies from buffering the stream.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
