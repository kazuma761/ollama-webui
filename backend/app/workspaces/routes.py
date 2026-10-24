"""/api/workspaces/... : workspaces and their documents (the Workspaces page and the chat's left panel).

The chat side is in `app/routes.py` (`/api/chat` with `workspace` set). The
work is in the top-level `workspaces/` folder (`workspace_engine`).
"""

import json
from collections.abc import AsyncIterator
from pathlib import PurePath

from document_engine.word import ACCEPTED, read_source
from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from ollama_pipeline import PipelineError, UnsupportedDocument, extract_pages
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from workspace_engine import WorkspaceError, WorkspaceService

router = APIRouter()

MAX_CHARS = 5_000_000  # text read from one file: about 2,500 pages
_PICTURES = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif")
_STREAM = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


class WorkspaceForm(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    instructions: str | None = Field(default=None, max_length=4000)


class SearchRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


def _service(request: Request) -> WorkspaceService:
    return request.app.state.workspaces


def _stream(events: AsyncIterator[dict]) -> StreamingResponse:
    async def lines() -> AsyncIterator[str]:
        try:
            async for event in events:
                yield json.dumps(event) + "\n"
        except (WorkspaceError, PipelineError) as exc:
            yield json.dumps({"type": "error", "message": str(exc)}) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson", headers=_STREAM)


def read_file(name: str, data: bytes) -> tuple[list[tuple[str, str]], bool, int | None]:
    """A file -> (sections, truncated, pages). A PDF keeps its pages, so a passage can name its page.

    Also used for files attached in the chat (app/routes.py)."""
    if PurePath(name).suffix.lower() in _PICTURES:
        raise UnsupportedDocument(f"{name} is a picture. Workspaces read text: PDF, Word, Excel, CSV and text files.")
    if PurePath(name).suffix.lower() == ".pdf" or data.startswith(b"%PDF-"):
        pages = extract_pages(name, data)
        sections, total = [], 0
        for number, text in enumerate(pages, 1):
            if total >= MAX_CHARS:
                return sections, True, len(pages)
            sections.append((f"page {number}", text[: MAX_CHARS - total]))
            total += len(text)
        return sections, False, len(pages)
    text, truncated = read_source(name, data, MAX_CHARS)
    return [("", text)], truncated, None


@router.get("")
async def workspaces(request: Request):
    service = _service(request)
    return {
        "workspaces": await run_in_threadpool(service.list),
        "embedder": await service.embedder(),
        "accepted": list(ACCEPTED),
        "max_upload_mb": service.config.max_upload_mb,
    }


@router.post("")
def create(form: WorkspaceForm, request: Request):
    try:
        return _service(request).create(form.name or "", form.instructions or "")
    except WorkspaceError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/{workspace_id}")
def detail(workspace_id: str, request: Request):
    service = _service(request)
    try:
        workspace = service.store.get(workspace_id)
    except WorkspaceError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {**service.summary(workspace), "documents": workspace.documents()}


@router.post("/{workspace_id}")
def update(workspace_id: str, form: WorkspaceForm, request: Request):
    """Renames a workspace or changes its instructions."""
    try:
        return _service(request).update(workspace_id, form.name, form.instructions)
    except WorkspaceError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/{workspace_id}")
def delete(workspace_id: str, request: Request):
    try:
        _service(request).delete(workspace_id)
    except WorkspaceError as exc:
        raise HTTPException(404, str(exc)) from exc
    request.app.state.memory.drop_scope(workspace_id)  # its memories go with it
    return {"ok": True}


@router.post("/{workspace_id}/documents")
async def add_document(workspace_id: str, file: UploadFile, request: Request):
    """Reads one file into the workspace. Streams progress, then `added` or `skipped`."""
    service = _service(request)
    try:
        service.store.get(workspace_id)
    except WorkspaceError as exc:
        raise HTTPException(404, str(exc)) from exc
    name = PurePath(file.filename or "document").name
    limit = service.config.max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"{name} is over {service.config.max_upload_mb} MB")

    async def events() -> AsyncIterator[dict]:
        yield {"type": "progress", "name": name, "message": "Reading the text"}
        try:
            sections, truncated, pages = await run_in_threadpool(read_file, name, data)
        except UnsupportedDocument as exc:
            raise WorkspaceError(str(exc)) from exc
        except ImportError as exc:
            raise WorkspaceError(f"Reading {name} needs the {exc.name} package on the server: run `pip install -r requirements.txt`.") from exc
        async for event in service.add(workspace_id, name, data, sections, truncated, pages):
            yield event

    return _stream(events())


@router.delete("/{workspace_id}/documents/{document_id}")
def remove_document(workspace_id: str, document_id: int, request: Request):
    try:
        removed = _service(request).remove(workspace_id, document_id)
    except WorkspaceError as exc:
        raise HTTPException(404, str(exc)) from exc
    if not removed:
        raise HTTPException(404, "That document is no longer in this workspace.")
    return {"ok": True}


@router.post("/{workspace_id}/reindex")
def reindex(workspace_id: str, request: Request):
    """Gives every passage a vector from the embedding model set now. Streams progress."""
    return _stream(_service(request).reindex(workspace_id))


@router.post("/{workspace_id}/search")
async def search(workspace_id: str, body: SearchRequest, request: Request):
    """The passages a question would be answered from, to try the index on the Workspaces page."""
    try:
        hits, mode = await _service(request).search(workspace_id, body.question)
    except WorkspaceError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"mode": mode, "hits": [h.__dict__ for h in hits]}
