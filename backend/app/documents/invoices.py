"""The HTTP routes of the Invoices tab, under /api/documents/invoices.

  /config   models that can read invoices, accepted file types, the sheet's columns
  /read     one file in -> a stream of progress, then a row per invoice found in it
  /export   the reviewed rows in -> an .xlsx or .csv file

Nothing is kept on the server: a file is read in memory and forgotten, and the
rows live in the browser until they are downloaded. The reading itself is in
the top-level `documents/` folder (`document_engine.invoices`).
"""

import time
from typing import Literal

from document_engine.invoices import ACCEPTED, COLUMNS, to_csv, to_xlsx
from document_engine.invoices.checks import EXPECTED, MISSING, SCORE, WEIGHT
from fastapi import APIRouter, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .routes import MAX_UPLOAD_BYTES, _stream

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

router = APIRouter()


class Row(BaseModel):
    file: str = ""
    pages: list[int] = Field(default_factory=list)
    kind: str = ""
    values: dict[str, str] = Field(default_factory=dict)
    levels: dict[str, str] = Field(default_factory=dict)
    notes: dict[str, str] = Field(default_factory=dict)
    confidence: float | None = None


class ExportRequest(BaseModel):
    rows: list[Row] = Field(max_length=5000)
    format: Literal["xlsx", "csv"] = "xlsx"
    checks: bool = Field(default=False, description="xlsx only: add a second sheet listing what to check and why")


@router.get("/config")
async def config(request: Request):
    service = request.app.state.invoices
    default, models = await service.models()
    return {
        "default": default, "models": models, "accepted": list(ACCEPTED), "columns": [list(c) for c in COLUMNS],
        "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024), "max_pages": service.config.max_pages,
        # How the Confidence column is worked out, so the page can redo it when a value is edited.
        "scoring": {"weight": WEIGHT, "score": SCORE, "missing": MISSING, "expected": EXPECTED},
    }


@router.post("/read")
async def read(request: Request, file: UploadFile, model: str | None = Form(default=None)):
    """Streams the reading of one invoice file."""
    name = file.filename or "invoice"
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"{name} is over {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    if not data:
        raise HTTPException(400, f"{name} is empty")
    return _stream(request.app.state.invoices.read(model or None, name, data))


@router.post("/export")
async def export(body: ExportRequest, request: Request):
    """The rows as shown on the page, as a file to download."""
    rows = [row.model_dump() for row in body.rows]
    if not rows:
        raise HTTPException(400, "There are no rows to download yet.")
    stamp = time.strftime("%Y-%m-%d_%H%M")
    if body.format == "csv":
        data, kind = to_csv(rows), "text/csv; charset=utf-8"
    else:
        date_format = request.app.state.invoices.config.date_format
        try:
            data, kind = await run_in_threadpool(to_xlsx, rows, date_format, body.checks), XLSX
        except ImportError as exc:
            raise HTTPException(500, "Excel files need the openpyxl package on the server: run `pip install -r requirements.txt`.") from exc
    return Response(data, media_type=kind, headers={"Content-Disposition": f'attachment; filename="invoices_{stamp}.{body.format}"'})
