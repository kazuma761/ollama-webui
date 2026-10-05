"""HTTP side of the Word-documents page: uploads, the stored files, and the streams.

All the work on the files is done by the `ollama_pipeline` package. This
module keeps each upload and its versions in a folder of its own, under an
unguessable id, and deletes the folder after `documents.keep_hours`.
"""

import json
import re
import secrets
import shutil
import time
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from ollama_pipeline import (
    PipelineError,
    UnsupportedDocument,
    WordFile,
    analyze_word,
    edit_word,
    fill_word,
    from_markdown,
    tidy_markdown,
)
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_JOB = re.compile(r"^[A-Za-z0-9_-]{20,40}$")
_STREAM = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

router = APIRouter()


class Store:
    """Uploaded files and the versions made from them, one folder per upload."""

    def __init__(self, root: Path, keep_hours: int):
        self.root, self.keep_seconds = root, keep_hours * 3600
        root.mkdir(parents=True, exist_ok=True)
        self.cleanup()

    def cleanup(self) -> None:
        for folder in self.root.iterdir():
            if folder.is_dir() and _JOB.match(folder.name):
                newest = max((f.stat().st_mtime for f in folder.iterdir()), default=folder.stat().st_mtime)
                if time.time() - newest > self.keep_seconds:
                    shutil.rmtree(folder, ignore_errors=True)

    def _folder(self, job: str) -> Path:
        folder = self.root / job
        if not _JOB.match(job) or not folder.is_dir():
            raise HTTPException(404, "That document is no longer on the server. Upload it again.")
        return folder

    def create(self, name: str, data: bytes, label: str = "Uploaded") -> str:
        self.cleanup()
        job = secrets.token_urlsafe(18)
        folder = self.root / job
        folder.mkdir()
        (folder / "v0.docx").write_bytes(data)
        (folder / "meta.json").write_text(json.dumps({"name": name, "versions": [label]}))
        return job

    def meta(self, job: str) -> dict:
        return json.loads((self._folder(job) / "meta.json").read_text())

    def read(self, job: str, version: int | None = None) -> tuple[str, bytes, int]:
        meta = self.meta(job)
        latest = len(meta["versions"]) - 1
        version = latest if version is None else version
        if not 0 <= version <= latest:
            raise HTTPException(404, "That version does not exist.")
        return meta["name"], (self._folder(job) / f"v{version}.docx").read_bytes(), version

    def add(self, job: str, data: bytes, label: str) -> int:
        folder, meta = self._folder(job), self.meta(job)
        meta["versions"].append(label)
        version = len(meta["versions"]) - 1
        (folder / f"v{version}.docx").write_bytes(data)
        (folder / "meta.json").write_text(json.dumps(meta))
        return version


class Source(BaseModel):
    name: str
    content: str


class ProposeRequest(BaseModel):
    model: str | None = None
    instructions: str = ""
    sources: list[Source] = Field(default_factory=list)


class FillRequest(BaseModel):
    values: dict[str, str]


class EditRequest(BaseModel):
    model: str | None = None
    instructions: str
    version: int | None = Field(default=None, description="Version to change; the latest when left out")


class CreateRequest(BaseModel):
    model: str | None = None
    instructions: str
    sources: list[Source] = Field(default_factory=list)
    style_job: str | None = Field(default=None, description="An uploaded file whose letterhead and styles the new document takes")


def _stream(events: AsyncIterator[dict]) -> StreamingResponse:
    async def lines() -> AsyncIterator[str]:
        try:
            async for event in events:
                yield json.dumps(event) + "\n"
        except (PipelineError, UnsupportedDocument) as exc:
            yield json.dumps({"type": "error", "message": str(exc)}) + "\n"
        except HTTPException as exc:
            yield json.dumps({"type": "error", "message": str(exc.detail)}) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson", headers=_STREAM)


def _download(job: str, version: int) -> str:
    return f"/api/documents/{job}/download?version={version}"


@router.get("/config")
async def config(request: Request):
    service = request.app.state.documents
    default, models = await service.models()
    return {
        "default": default, "models": models, "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
        "keep_hours": service.config.keep_hours, "cloud": service.config.allow_cloud,
    }


@router.post("/upload")
def upload(request: Request, file: UploadFile):
    """Stores a .docx or .dotx and returns its blanks and a text preview."""
    name = file.filename or "document.docx"
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"{name} is over 20 MB")
    try:
        analysis = analyze_word(name, data)
    except UnsupportedDocument as exc:
        raise HTTPException(415, str(exc)) from exc
    job = request.app.state.store.create(name, data)
    return {"job": job, "version": 0, **analysis}


@router.post("/{job}/propose")
async def propose(job: str, body: ProposeRequest, request: Request):
    """Streams suggested values for the blanks of the uploaded template."""
    name, data, _ = request.app.state.store.read(job, 0)
    fields = (await run_in_threadpool(analyze_word, name, data))["fields"]
    sources = [s.model_dump() for s in body.sources]
    return _stream(request.app.state.documents.propose(body.model, fields, body.instructions, sources))


@router.post("/{job}/fill")
def fill(job: str, body: FillRequest, request: Request):
    """Writes the reviewed values into the template and stores the result as a new version."""
    store = request.app.state.store
    name, data, _ = store.read(job, 0)
    result, report = fill_word(name, data, body.values)
    version = store.add(job, result, "Filled")
    return {"job": job, "version": version, "download": _download(job, version), **report}


@router.post("/{job}/edit")
async def edit(job: str, body: EditRequest, request: Request):
    """Streams the model's plan, applies it, and stores the changed file as a new version."""
    store = request.app.state.store
    name, data, version = store.read(job, body.version)

    async def events() -> AsyncIterator[dict]:
        outline = await run_in_threadpool(lambda: WordFile(name, data).outline())
        async for event in request.app.state.documents.plan(body.model, outline, body.instructions):
            if event["type"] != "operations":
                yield event
                continue
            result, report = await run_in_threadpool(edit_word, name, data, event["operations"])
            new = store.add(job, result, "Edited") if report["applied"] else version
            yield {
                "type": "result", "job": job, "version": new, "download": _download(job, new),
                "summary": event["summary"], **report,
            }

    return _stream(events())


@router.post("/create")
async def create(body: CreateRequest, request: Request):
    """Streams a new document as it is written, then builds the Word file from it."""
    store = request.app.state.store
    base = store.read(body.style_job, 0)[:2] if body.style_job else None
    sources = [s.model_dump() for s in body.sources]

    async def events() -> AsyncIterator[dict]:
        written = ""
        async for event in request.app.state.documents.write(body.model, body.instructions, sources):
            if event["type"] == "delta":
                written += event["content"]
            yield event
        markdown = tidy_markdown(written)
        if not markdown:
            raise PipelineError("The model wrote nothing. Try again or pick another model.")
        result = await run_in_threadpool(from_markdown, markdown, base)
        title = re.search(r"^#\s+(.+)$", markdown, re.MULTILINE)
        name = re.sub(r'[\\/:*?"<>|]+', " ", title.group(1)).strip()[:80] if title else "Document"
        job = store.create(f"{name}.docx", result, "Written")
        preview = await run_in_threadpool(lambda: WordFile(name, result).preview())
        yield {"type": "result", "job": job, "version": 0, "download": _download(job, 0), "name": f"{name}.docx", "preview": preview}

    return _stream(events())


@router.get("/{job}/download")
def download(job: str, request: Request, version: int | None = None):
    store = request.app.state.store
    name, _, version = store.read(job, version)
    label = store.meta(job)["versions"][version].lower()
    stem = re.sub(r"\.(docx|dotx)$", "", name, flags=re.IGNORECASE)
    filename = f"{stem}.docx" if label in ("uploaded", "written") else f"{stem} ({label}).docx"
    return FileResponse(store.root / job / f"v{version}.docx", media_type=DOCX, filename=filename)
