"""The model's part of the invoices tab: each page goes to a local model, which returns
the fields it reads. What comes back is cleaned, checked and scored by `checks.py`.

Only local (Ollama) models are used, always: invoices never go to a cloud model,
whatever `documents.allow_cloud` says. A model that reads pictures is needed for
scans and photos; a text-only model can still read PDFs and Word files that have
real text in them.
"""

from __future__ import annotations

import asyncio
import base64
import time
from collections.abc import AsyncIterator
from typing import Any

from ollama_pipeline import ModelEntry, OllamaError, OllamaUnavailable, PipelineError, Registry

from . import prompts
from .checks import clean, merge
from .reader import FileRead, Page, read_file

MAX_ANSWER_TOKENS = 6000  # a page with many lines needs a few thousand; beyond this it is a runaway answer


def _list(numbers: list[int]) -> str:
    return ("page " if len(numbers) == 1 else "pages ") + ", ".join(str(n) for n in numbers)


class InvoiceService:
    def __init__(self, registry: Registry):
        self.registry = registry
        self.config = registry.config.invoices
        self._slots = asyncio.Semaphore(self.config.max_jobs)

    async def models(self) -> tuple[str | None, list[dict[str, Any]]]:
        """The local models this tab may use, and which one it starts with: the configured
        one, else the first that reads pictures."""
        local = [e for e in await self.registry.list_models() if e.provider == "ollama"]
        usable = [e for e in local if e.available]
        default = next((e.id for e in usable if e.id == self.config.model), None)
        default = default or next((e.id for e in usable if e.vision), usable[0].id if usable else None)
        return default, [e.public() for e in local]

    async def _entry(self, model_id: str | None) -> ModelEntry:
        if not model_id:
            model_id, _ = await self.models()
        entry = await self.registry.resolve(model_id)
        if entry.provider != "ollama":
            raise PipelineError("Invoices are only read by local models. Pick one of the Ollama models.")
        return entry

    async def _ask(self, entry: ModelEntry, page: Page, total: int, picture: bool) -> list[dict[str, Any]] | None:
        """The documents the model finds on one page, or None when its answer is unusable."""
        message: dict[str, Any] = {"role": "user", "content": prompts.page_request(page.number, total, page.text, picture)}
        if picture:
            message["images"] = [base64.b64encode(page.image).decode("ascii")]
        # Temperature 0: the same page should give the same answer every time.
        options = {**entry.options, "temperature": 0, "num_predict": MAX_ANSWER_TOKENS}
        answer = await self.registry.client(entry.host).chat_json(
            entry.model, [{"role": "system", "content": prompts.SYSTEM}, message], prompts.SCHEMA, options,
            think=False if entry.thinking else None,
        )
        found = answer.get("documents") if isinstance(answer, dict) else None
        return [d for d in found if isinstance(d, dict)] if isinstance(found, list) else None

    async def read(self, model_id: str | None, name: str, data: bytes) -> AsyncIterator[dict[str, Any]]:
        """Reads one file. Yields progress, then one `rows` event with a row per invoice found."""
        entry = await self._entry(model_id)
        started = time.monotonic()
        yield {"type": "progress", "message": "Opening the file"}
        try:
            file: FileRead = await asyncio.to_thread(read_file, name, data, self.config.max_pages, self.config.image_side)
        except ImportError as exc:
            raise PipelineError(
                f"The invoice reader needs a package that is not installed on the server ({exc.name}). "
                "Run `pip install -r requirements.txt` in the project folder (or `uv sync --extra router --inexact` in backend/), then restart."
            ) from exc
        sees = entry.vision is not False  # None: the server did not say - try, and fall back to the text
        count = len(file.pages)

        if self._slots.locked():
            yield {"type": "progress", "message": "Waiting for another invoice to finish"}
        async with self._slots:
            yield {"type": "model", "id": entry.id, "label": entry.label, "pictures": bool(entry.vision)}
            rows: list[dict[str, Any]] = []
            stopped = ""
            unreadable: list[int] = []
            failed: list[int] = []
            for page in file.pages:
                yield {"type": "progress", "message": f"Reading page {page.number} of {count}", "page": page.number, "pages": count}
                picture = bool(page.image) and sees
                if not page.text and not picture:
                    unreadable.append(page.number)
                    continue
                try:
                    found = await self._ask(entry, page, count, picture)
                except OllamaUnavailable as exc:
                    if not rows:
                        raise
                    # Part-way through a long file: keep what was read rather than lose it all.
                    stopped = f"Stopped at page {page.number} of {count}: {exc} The rows below are from the pages before it."
                    break
                except OllamaError as exc:
                    if not (picture and page.text):
                        raise PipelineError(f"{entry.label} could not read page {page.number}: {exc}") from exc
                    sees = False  # it does not take pictures after all: go on with the text
                    found = await self._ask(entry, page, count, False)
                if found is None:
                    failed.append(page.number)
                    continue
                for raw in found:
                    row = clean(raw, page.text, self.config.date_format)
                    if row:
                        rows.append({"file": name, "pages": [page.number], **row})
            rows = merge(rows)

        yield {"type": "rows", "rows": rows}
        for note in (*file.notes, stopped):
            if note:
                yield {"type": "notice", "message": note}
        if unreadable:
            yield {"type": "notice", "message": (
                f"{_list(unreadable).capitalize()} of this file {'is a scan or photo' if len(unreadable) == 1 else 'are scans or photos'}, "
                f"and {entry.label} cannot read pictures. Choose a model marked \"reads pictures\"."
            )}
        if failed:
            yield {"type": "notice", "message": f"The model's answer for {_list(failed)} could not be used. Try again, or try another model."}
        if not rows and not unreadable and not failed and not stopped:
            yield {"type": "notice", "message": "No invoice, bill or receipt was found in this file."}
        yield {"type": "done", "pages": count, "rows": len(rows), "seconds": round(time.monotonic() - started, 1)}
