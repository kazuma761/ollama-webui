"""The model's part of the Word tab: the sources and the instructions go to a local model,
which writes the document as Markdown. `build.py` turns that into the Word file.

Only local (Ollama) models are used, always: source files never go to a cloud
model, whatever `documents.allow_cloud` says.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from ollama_pipeline import ModelEntry, PipelineError, Registry

from . import prompts

CHARS_PER_TOKEN = 3.0  # deliberately low, so the sources surely fit
ANSWER_TOKENS = 4000   # room kept in the context for the instructions and the document itself


def share(sources: list[dict[str, str]], limit: int) -> tuple[str, list[str]]:
    """The sources as one text of at most `limit` characters, and the names of those cut short.

    Every file gets an equal part of the room; what a short file does not use
    goes to the longer ones. So a long first file cannot push the others out.
    """
    left = [(s["name"], s.get("content", "").strip()) for s in sources if s.get("content", "").strip()]
    parts: dict[str, str] = {}
    cut: list[str] = []
    for position, (name, text) in enumerate(sorted(left, key=lambda item: len(item[1]))):
        room = max(0, limit - sum(len(t) for t in parts.values())) // (len(left) - position)
        if len(text) > room:
            cut.append(name)
        parts[name] = text[:room]
    return "\n\n".join(f"--- {name} ---\n{parts[name]}" for name, _ in left), cut


class WordService:
    def __init__(self, registry: Registry):
        self.registry = registry
        self.config = registry.config.documents
        self._slots = asyncio.Semaphore(self.config.max_jobs)

    async def models(self) -> tuple[str | None, list[dict[str, Any]]]:
        """The local models this tab may use, and which one it starts with."""
        local = [e for e in await self.registry.list_models() if e.provider == "ollama"]
        usable = [e.id for e in local if e.available]
        config = self.registry.config
        heavy = config.router.route(config.router.heavy_route)
        preferred = [m for m in (self.config.model, *(heavy.models if heavy else ()), config.default_model) if m]
        default = next((m for m in preferred if m in usable), usable[0] if usable else None)
        return default, [e.public() for e in local]

    async def _entry(self, model_id: str | None) -> ModelEntry:
        if not model_id:
            model_id, _ = await self.models()
        entry = await self.registry.resolve(model_id)
        if entry.provider != "ollama":
            raise PipelineError("Source files are only read by local models. Pick one of the Ollama models.")
        return entry

    async def write(
        self, model_id: str | None, instructions: str, sources: list[dict[str, str]], template: str = "blank"
    ) -> AsyncIterator[dict[str, Any]]:
        """Writes the document as Markdown, piece by piece as the model produces it."""
        entry = await self._entry(model_id)
        if not instructions.strip() and not sources:
            raise PipelineError("Add source files or say what the document should be about.")
        context = entry.budget  # llama-server's slot size, or num_ctx for Ollama
        limit = max(3000, int((context - ANSWER_TOKENS) * CHARS_PER_TOKEN))
        source_text, cut = share(sources, limit)
        if self._slots.locked():
            yield {"type": "progress", "message": "Waiting for other document jobs to finish"}
        async with self._slots:
            yield {"type": "model", "id": entry.id, "label": entry.label}
            if cut:
                yield {"type": "notice", "message": (
                    f"{', '.join(cut)}: longer than {entry.label} can read beside the other files, so only the first part "
                    "was used. Add fewer files, or give the model more context (llama-server's -c, or num_ctx for Ollama)."
                )}
            request = (
                f"KIND OF DOCUMENT: {prompts.KINDS.get(template, prompts.KINDS['blank'])}\n\n"
                f"INSTRUCTIONS:\n{instructions.strip() or 'Write the document from the source files.'}"
            )
            if source_text:
                request += f"\n\nSOURCE FILES ({len([s for s in sources if s.get('content', '').strip()])}):\n{source_text}"
            messages = [{"role": "system", "content": prompts.WRITE_SYSTEM}, {"role": "user", "content": request}]
            async for chunk in self.registry.client(entry.host).chat_stream(entry.model, messages, entry.options):
                message = chunk.get("message") or {}
                if message.get("thinking"):
                    yield {"type": "thinking", "content": message["thinking"]}
                if message.get("content"):
                    yield {"type": "delta", "content": message["content"]}
