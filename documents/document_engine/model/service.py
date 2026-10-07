"""The model's part in working on Word files: it decides, it never writes the file.

Three jobs, each a stream of events for the page to show as they happen:

- `propose`  a template's blanks + the user's instructions and source text
             -> one value per blank, for the user to review before anything is written
- `plan`     a document's outline + a request to change it
             -> a list of operations from `engine/wordedit.py`
- `write`    instructions and source text -> a document written as Markdown

For the first two Ollama holds the answer to a JSON schema, so what comes
back always has the expected shape. Whether it is *right* is a different
question, which is why every value is checked against the source text here
and shown to the user before a file is made.

Only local (Ollama) models are used. Documents are never sent to a cloud model.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from typing import Any

from ollama_pipeline import ModelEntry, PipelineError, Registry
from ..engine import slotmap as slots
from ..engine.wordedit import OPERATIONS
from ..engine.wordfile import WordFile, paragraph_text
from . import prompts

BATCH = 12  # blanks asked for in one request; small models lose track of longer lists
CHARS_PER_TOKEN = 3.0  # deliberately low, so a source chunk surely fits
PROMPT_TOKENS = 2200  # room kept for the instructions, the list of blanks and the answer
NOT_AN_ANSWER = {"", "n/a", "na", "none", "null", "unknown", "not found", "not provided", "not available", "not specified", "-", "--", "tbd"}

def _plain(text: str) -> str:
    """Lower case, punctuation and spacing ignored - for telling whether a value appears in a text."""
    return " ".join(re.sub(r"[\W_]+", " ", text.lower()).split())


def origin_of(value: str, sources: str, instructions: str) -> str:
    """Where a proposed value comes from: "source", "prompt", or "model" when it appears in neither."""
    wanted = _plain(value)
    if not wanted:
        return ""
    if wanted in _plain(sources):
        return "source"
    if wanted in _plain(instructions):
        return "prompt"
    return "model"


def _clean(value: Any, field: dict[str, Any]) -> str:
    """A model's answer for one blank, with the usual non-answers and echoes removed."""
    if not isinstance(value, str):
        return ""
    value = value.strip().strip('"“”').strip()
    if value.lower() in NOT_AN_ANSWER or _plain(value) in (_plain(field.get("current") or ""), _plain(field["label"])):
        return ""
    options = field.get("options") or []
    if options:
        return next((option for option in options if _plain(option) == _plain(value)), "")
    return value


def _chunks(text: str, limit: int) -> list[str]:
    """Splits long source text at paragraph ends into pieces a model can read in one go."""
    if len(text) <= limit:
        return [text]
    pieces, current = [], ""
    for paragraph in text.split("\n"):
        while len(paragraph) > limit:  # one enormous paragraph
            pieces.append(paragraph[:limit])
            paragraph = paragraph[limit:]
        if current and len(current) + len(paragraph) + 1 > limit:
            pieces.append(current)
            current = ""
        current = f"{current}\n{paragraph}" if current else paragraph
    if current.strip():
        pieces.append(current)
    return pieces


def _sources_text(sources: list[dict[str, str]]) -> str:
    return "\n\n".join(f"--- {s['name']} ---\n{s['content'].strip()}" for s in sources if s.get("content", "").strip())


class DocumentService:
    def __init__(self, registry: Registry):
        self.registry = registry
        self.config = registry.config.documents
        self._slots = asyncio.Semaphore(self.config.max_jobs)

    # ── Models ──

    def _preferred(self) -> list[str]:
        config = self.registry.config
        heavy = config.router.route(config.router.heavy_route)
        return [m for m in (self.config.model, *(heavy.models if heavy else ()), config.default_model) if m]

    async def models(self) -> tuple[str | None, list[dict[str, Any]]]:
        """The local models this page may use, and which one it starts with."""
        local = [e for e in await self.registry.list_models() if e.provider == "ollama" or self.config.allow_cloud]
        usable = [e.id for e in local if e.available]
        default = next((m for m in self._preferred() if m in usable), usable[0] if usable else None)
        return default, [e.public() for e in local]

    async def _entry(self, model_id: str | None) -> ModelEntry:
        if not model_id:
            model_id, _ = await self.models()
        entry = await self.registry.resolve(model_id)
        if entry.provider != "ollama" and not self.config.allow_cloud:
            raise PipelineError("Documents are only worked on by local models. Pick one of the Ollama models.")
        return entry

    def _budget(self, entry: ModelEntry) -> int:
        """How many characters of source text fit beside the rest of a request."""
        if entry.provider != "ollama":
            return 60_000
        context = int(entry.options.get("num_ctx") or 4096)
        return max(3000, int((context - PROMPT_TOKENS) * CHARS_PER_TOKEN))

    async def _ask(self, entry: ModelEntry, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        if entry.provider != "ollama":
            # Cloud stand-in for testing (documents.allow_cloud): no schema, so the JSON is fished out of the reply.
            reply = ""
            async for event in self.registry.opencode.chat_stream(entry.model, [{"role": "user", "content": f"{system}\n\n{user}"}]):
                if event["type"] == "delta":
                    reply += event["content"]
            found = re.search(r"\{.*\}", reply, re.DOTALL)
            try:
                answer = json.loads(found.group(0)) if found else {}
            except ValueError:
                answer = {}
            return answer if isinstance(answer, dict) else {}
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        options = {**entry.options, "temperature": 0.1}  # the same question should get the same answer
        answer = await self.registry.client(entry.host).chat_json(
            entry.model, messages, schema, options, think=False if entry.thinking else None
        )
        return answer if isinstance(answer, dict) else {}

    # ── Filling a template ──

    async def propose(
        self, model_id: str | None, fields: list[dict[str, Any]], instructions: str, sources: list[dict[str, str]]
    ) -> AsyncIterator[dict[str, Any]]:
        """Suggests a value for each blank. Yields progress, then `values` events as answers arrive."""
        entry = await self._entry(model_id)
        instructions = instructions.strip()
        source_text = _sources_text(sources)
        if not instructions and not source_text:
            raise PipelineError("Say what should go into the template, or add a file to take it from.")
        chunks = _chunks(source_text, self._budget(entry)) if source_text else [""]
        texts = [f for f in fields if f["kind"] != "section"]
        sections = [f for f in fields if f["kind"] == "section"]

        if self._slots.locked():
            yield {"type": "progress", "message": "Waiting for other document jobs to finish"}
        async with self._slots:
            yield {"type": "model", "id": entry.id, "label": entry.label}
            found: dict[str, str] = {}

            for start in range(0, len(texts), BATCH):
                batch = texts[start:start + BATCH]
                yield {"type": "progress", "message": f"Blanks {start + 1} to {start + len(batch)} of {len(texts)}"}
                for number, chunk in enumerate(chunks, 1):
                    waiting = [f for f in batch if f["id"] not in found]
                    if not waiting:
                        break
                    if len(chunks) > 1:
                        yield {"type": "progress", "message": f"Reading part {number} of {len(chunks)} of your files"}
                    answer = await self._ask(entry, prompts.FILL_SYSTEM, self._fill_request(waiting, instructions, chunk), {
                        "type": "object",
                        "properties": {f["id"]: {"type": "string"} for f in waiting},
                        "required": [f["id"] for f in waiting],
                    })
                    new = {f["id"]: value for f in waiting if (value := _clean(answer.get(f["id"]), f))}
                    found.update(new)
                    if new:
                        yield {"type": "values", "values": {
                            key: {"value": value, "origin": origin_of(value, source_text, instructions)} for key, value in new.items()
                        }}

            for number, section in enumerate(sections, 1):
                yield {"type": "progress", "message": f"Writing section {number} of {len(sections)}: {section['label']}"}
                request = (
                    f"INSTRUCTIONS FROM THE USER:\n{instructions or '(none)'}\n\n"
                    f"SOURCE TEXT:\n{chunks[0] or '(no source documents)'}\n\n"
                    f"SECTION HEADING: {section['label']}\n\nAnswer as JSON: {{\"text\": \"...\"}}"
                )
                answer = await self._ask(entry, prompts.SECTION_SYSTEM, request, {
                    "type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"],
                })
                text = _clean(answer.get("text"), section)
                if text:
                    found[section["id"]] = text
                    yield {"type": "values", "values": {section["id"]: {"value": text, "origin": origin_of(text, source_text, instructions)}}}

            if self.config.second_check:
                doubtful = [f for f in texts if f["id"] in found and origin_of(found[f["id"]], source_text, instructions) == "model"]
                if doubtful:
                    yield {"type": "progress", "message": f"Checking {len(doubtful)} value{'s' if len(doubtful) != 1 else ''} the model did not copy"}
                    listing = "\n".join(f'{f["id"]}: "{f["label"]}" = "{found[f["id"]]}"' for f in doubtful)
                    request = (
                        f"INSTRUCTIONS FROM THE USER:\n{instructions or '(none)'}\n\n"
                        f"SOURCE TEXT:\n{chunks[0] or '(no source documents)'}\n\nVALUES TO CHECK:\n{listing}"
                    )
                    verdict = await self._ask(entry, prompts.CHECK_SYSTEM, request, {
                        "type": "object",
                        "properties": {f["id"]: {"type": "boolean"} for f in doubtful},
                        "required": [f["id"] for f in doubtful],
                    })
                    rejected = {f["id"]: {"value": found[f["id"]], "origin": "unsupported"} for f in doubtful if verdict.get(f["id"]) is False}
                    if rejected:
                        yield {"type": "values", "values": rejected}

            yield {"type": "done", "filled": len(found), "blanks": len(fields)}

    @staticmethod
    def _fill_request(fields: list[dict[str, Any]], instructions: str, source: str) -> str:
        lines = []
        for item in fields:
            line = f'{item["id"]}: "{item["label"]}" - in the template: {item["context"]}'
            if item.get("options"):
                line += f' - options: {" | ".join(item["options"])}'
            lines.append(line)
        example = ", ".join(f'"{f["id"]}": "..."' for f in fields[:2])
        return (
            f"INSTRUCTIONS FROM THE USER:\n{instructions or '(none)'}\n\n"
            f"SOURCE TEXT:\n{source or '(no source documents)'}\n\n"
            "BLANKS TO FILL:\n" + "\n".join(lines) + f"\n\nAnswer as JSON: {{{example}, ...}}"
        )

    # ── Preparing a designed template (once) ──

    async def prepare(self, model_id: str | None, word: WordFile) -> AsyncIterator[dict[str, Any]]:
        """Labels every paragraph of a template. Yields progress, then one `labels` event."""
        entry = await self._entry(model_id)
        labels = slots.guess(word)
        asked, lines = [], []
        for block in word.blocks:
            text = " ".join(paragraph_text(block.element).split())
            if not text or block.where not in ("body", "textbox"):
                continue
            shown = text if len(text) <= 150 else text[:147] + "..."
            notes = []
            if block.where == "textbox":
                notes.append("text box")
            if word.style_name(block.element).lower().startswith("list"):
                notes.append("bullet")
            if block.id in labels:
                lines.append((None, f"{block.id} (section heading): {shown}"))
                continue
            asked.append(block.id)
            lines.append((block.id, f"{block.id}{' [' + ', '.join(notes) + ']' if notes else ''}: {shown}"))

        # Too long for one request: cut at section headings, never in the middle of a section.
        limit = self._budget(entry)
        parts: list[list[tuple[str | None, str]]] = [[]]
        for line in lines:
            if line[0] is None and sum(len(text) for _, text in parts[-1]) > limit * 0.6:
                parts.append([])
            parts[-1].append(line)

        if self._slots.locked():
            yield {"type": "progress", "message": "Waiting for other document jobs to finish"}
        async with self._slots:
            yield {"type": "model", "id": entry.id, "label": entry.label}
            for number, part in enumerate(parts, 1):
                ids = [block_id for block_id, _ in part if block_id]
                if not ids:
                    continue
                yield {"type": "progress", "message": f"Labelling the template's {len(asked)} lines" + (f" (part {number} of {len(parts)})" if len(parts) > 1 else "")}
                one = {
                    "type": "object",
                    "properties": {
                        "role": {"type": "string", "enum": ["fixed", "field", "group_item"]},
                        "name": {"type": "string"}, "group": {"type": "string"}, "instance": {"type": "integer"},
                    },
                    "required": ["role", "name", "group", "instance"],
                }
                listing = "\n".join(text for _, text in part)
                answer = await self._ask(entry, prompts.PREPARE_SYSTEM, f"TEMPLATE:\n{listing}\n\nLabel these ids: {', '.join(ids)}", {
                    "type": "object", "properties": {block_id: one for block_id in ids}, "required": ids,
                })
                for block_id in ids:
                    label = answer.get(block_id)
                    if isinstance(label, dict) and label.get("role") in ("fixed", "field", "group_item"):
                        labels[block_id] = {
                            "role": label["role"], "name": slots.snake(label.get("name")),
                            "group": slots.snake(label.get("group")) if label["role"] == "group_item" else "",
                            "instance": label.get("instance") if label["role"] == "group_item" and isinstance(label.get("instance"), int) else 0,
                        }
            yield {"type": "labels", "labels": labels, "asked": len(asked)}

    # ── Filling a prepared template ──

    async def extract(
        self, model_id: str | None, word: WordFile, slotmap: dict[str, Any], instructions: str, sources: list[dict[str, str]]
    ) -> AsyncIterator[dict[str, Any]]:
        """Pulls the user's content into the slot map's shape. Yields progress and `values` events."""
        entry = await self._entry(model_id)
        instructions = instructions.strip()
        source_text = _sources_text(sources)
        if not instructions and not source_text:
            raise PipelineError("Add a file to take the content from, or say what should go in.")
        limit = self._budget(entry)
        if len(source_text) > limit:
            yield {"type": "notice", "message": f"Your files are longer than {entry.label} can read at once; only the first part was used."}
        source = source_text[:limit]
        # A short source goes in one request; a long one is asked about one group at a time,
        # so each answer stays small enough for a small model to get right.
        whole = len(source) < 6000
        parts: list[str | None] = [None] if whole else ["", *[g["name"] for g in slotmap["groups"]]]

        if self._slots.locked():
            yield {"type": "progress", "message": "Waiting for other document jobs to finish"}
        async with self._slots:
            yield {"type": "model", "id": entry.id, "label": entry.label}
            for number, only in enumerate(parts, 1):
                schema = slots.values_schema(slotmap, only)
                if not schema["properties"]:
                    continue
                what = "everything" if only is None else ("the single values" if only == "" else only.replace("_", " "))
                yield {"type": "progress", "message": f"Reading your content: {what}" + (f" ({number} of {len(parts)})" if len(parts) > 1 else "")}
                request = (
                    f"INSTRUCTIONS FROM THE USER:\n{instructions or '(none)'}\n\n"
                    f"SOURCE TEXT:\n{source or '(no source documents)'}\n\n"
                    f"SLOTS TO FILL:\n{slots.describe(word, slotmap, only)}"
                )
                answer = await self._ask(entry, prompts.EXTRACT_SYSTEM, request, schema)
                values = {key: answer[key] for key in schema["properties"] if key in answer}
                yield {"type": "values", "values": values, "origins": _origins(values, source_text, instructions)}
            yield {"type": "done"}

    # ── Changing a document ──

    async def plan(self, model_id: str | None, outline: str, instructions: str) -> AsyncIterator[dict[str, Any]]:
        """Turns a request into a list of operations. Yields progress, then one `operations` event."""
        entry = await self._entry(model_id)
        if not instructions.strip():
            raise PipelineError("Say what should change in the document.")
        limit = self._budget(entry)
        if len(outline) > limit:
            raise PipelineError(
                f"This document is too long for {entry.label} to read in one go "
                f"(about {len(outline) // 1000}k characters, room for {limit // 1000}k). Try a shorter document."
            )
        if self._slots.locked():
            yield {"type": "progress", "message": "Waiting for other document jobs to finish"}
        async with self._slots:
            yield {"type": "model", "id": entry.id, "label": entry.label}
            yield {"type": "progress", "message": "Working out the changes"}
            system = prompts.EDIT_SYSTEM.format(operations="\n".join(f"- {name}: {text}" for name, text in OPERATIONS.items()))
            answer = await self._ask(entry, system, f"DOCUMENT:\n{outline}\n\nREQUEST:\n{instructions.strip()}", prompts.EDIT_SCHEMA)
            operations = answer.get("operations")
            yield {
                "type": "operations",
                "operations": operations if isinstance(operations, list) else [],
                "summary": answer.get("summary") if isinstance(answer.get("summary"), str) else "",
            }

    # ── Writing a new document ──

    async def write(
        self, model_id: str | None, instructions: str, sources: list[dict[str, str]]
    ) -> AsyncIterator[dict[str, Any]]:
        """Writes a document as Markdown, piece by piece as the model produces it."""
        entry = await self._entry(model_id)
        if not instructions.strip():
            raise PipelineError("Say what the document should be about.")
        source_text = _sources_text(sources)
        limit = self._budget(entry)
        if self._slots.locked():
            yield {"type": "progress", "message": "Waiting for other document jobs to finish"}
        async with self._slots:
            yield {"type": "model", "id": entry.id, "label": entry.label}
            if len(source_text) > limit:
                yield {"type": "notice", "message": f"Your files are longer than {entry.label} can read at once; only the first part was used."}
            request = f"INSTRUCTIONS:\n{instructions.strip()}"
            if source_text:
                request += f"\n\nSOURCE TEXT:\n{source_text[:limit]}"
            if entry.provider != "ollama":
                async for event in self.registry.opencode.chat_stream(entry.model, [{"role": "user", "content": f"{prompts.WRITE_SYSTEM}\n\n{request}"}]):
                    if event["type"] in ("delta", "thinking"):
                        yield event
                return
            messages = [{"role": "system", "content": prompts.WRITE_SYSTEM}, {"role": "user", "content": request}]
            async for chunk in self.registry.client(entry.host).chat_stream(entry.model, messages, entry.options):
                message = chunk.get("message") or {}
                if message.get("thinking"):
                    yield {"type": "thinking", "content": message["thinking"]}
                if message.get("content"):
                    yield {"type": "delta", "content": message["content"]}


def _origins(values: dict[str, Any], sources: str, instructions: str) -> dict[str, str]:
    """Where each extracted value comes from, keyed like `jobs.0.bullets.2`."""
    found: dict[str, str] = {}

    def walk(value: Any, path: str) -> None:
        if isinstance(value, str):
            if value.strip():
                found[path] = origin_of(value, sources, instructions)
        elif isinstance(value, list):
            for number, item in enumerate(value):
                walk(item, f"{path}.{number}")
        elif isinstance(value, dict):
            for key, item in value.items():
                walk(item, f"{path}.{key}" if path else key)

    walk(values, "")
    return found


def tidy_markdown(text: str) -> str:
    """Removes the code fence some models put around a whole answer."""
    text = text.strip()
    fenced = re.fullmatch(r"```(?:markdown|md)?\s*\n(.*?)\n```", text, re.DOTALL)
    return fenced.group(1).strip() if fenced else text
