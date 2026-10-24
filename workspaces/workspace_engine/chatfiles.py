"""Files attached in the chat: kept on this machine, and only what a question needs goes to the model.

Before, a file's whole text went into the chat message, and the browser sent
it again with every later message. A 100-page PDF is about 60,000 tokens: far
more than one request to llama-server can hold (its slot: `-c` / `-np`), so
the request was refused or the model saw only part of the file. That was the
"it loses the context with big files" problem.

Now the file stays here, under a random id, for `documents.keep_hours`:

- its text is split into passages and indexed (words always, meaning too when
  the embedding model runs: in the background, so the upload is quick);
- a spreadsheet is loaded into SQLite by the SQL connector (the backend does
  that), and the model queries it rather than reading it;
- the chat message carries only the id. For each question:
    * files that fit are given whole;
    * from the others, the passages that match the question, as many as fit;
    * for a summary of a file too long to read at once, `read_whole` reads it
      in parts that fit, notes each one, and the answer is written from the
      notes (the notes are kept, so a second question does not read it again).

How much "fits" comes from the model's real context (llama-server's slot
size), worked out by the backend for each request.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
import shutil
import time
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from itertools import islice
from pathlib import Path
from typing import Any

from ollama_pipeline import ContextOverflow, ModelEntry, PipelineError, Registry, cut_middle, estimate

from . import prompts
from .chunking import passages as split_passages, split
from .embedding import Embedder
from .index import Hit, Workspace

log = logging.getLogger(__name__)

_ID = re.compile(r"^[A-Za-z0-9_-]{16,40}$")
_WHOLE = re.compile(
    r"\b(summar(y|ies|ise|ize|ising|izing)|overview|tl;?dr|gist|key (points|takeaways|findings)|main points|"
    r"(whole|entire|full) (document|file|pdf|report|text|contract)|go through (the|this|it)|read (the|this|it) (all|whole)|"
    r"what('?s| is) (this|the) (document|file|pdf|report) about)\b",
    re.I,
)
SNIPPET = 600  # characters of a passage shown under an answer
MAX_PARTS = 40  # parts of one file read for a summary: about 250 pages
NOTE_TOKENS = 400  # longest note on one part
CONCURRENT_PARTS = 2  # parts read at the same time for one person (llama-server serves others meanwhile)


def wants_whole(text: str) -> bool:
    """Whether a question is about a file as a whole (a summary), not one fact in it."""
    return bool(_WHOLE.search(text or ""))


@dataclass
class ChatFile:
    id: str
    name: str
    kind: str  # "text", or "table" for a spreadsheet
    chars: int
    tokens: int
    pages: int | None = None
    truncated: bool = False
    created: float = 0.0
    query: bool = False  # a big spreadsheet: queried with SQL, not read
    tables: list[dict[str, Any]] = field(default_factory=list)
    vectors: str = "none"  # "pending", "ready", "failed" or "none"
    folder: Path | None = None

    @property
    def db_id(self) -> str:
        """What the model calls this file's tables (the SQL tools' database_id)."""
        return self.name

    @property
    def tables_path(self) -> Path:
        return self.folder / "tables.db"

    def public(self) -> dict[str, Any]:
        """For the browser: what the file is, not its text."""
        rows = sum(t["rows"] for t in self.tables)
        return {"id": self.id, "name": self.name, "kind": self.kind, "chars": self.chars, "tokens": self.tokens,
                "pages": self.pages, "truncated": self.truncated, "sheets": len(self.tables), "rows": rows,
                "query": self.query}


class ChatFiles:
    def __init__(self, registry: Registry, root: Path, embedding: Embedder, keep_hours: int = 24):
        self.registry = registry
        self.root = root
        self.embedding = embedding
        self.keep_seconds = keep_hours * 3600
        self.config = registry.config.workspaces
        self._tasks: set[asyncio.Task] = set()
        root.mkdir(parents=True, exist_ok=True)

    # ── keeping files ──
    def new(self) -> tuple[str, Path]:
        """An id and an empty folder for a file about to be added."""
        self.cleanup()
        file_id = secrets.token_urlsafe(18)
        folder = self.root / file_id
        folder.mkdir()
        return file_id, folder

    def discard(self, file_id: str) -> None:
        if _ID.match(file_id):
            shutil.rmtree(self.root / file_id, ignore_errors=True)

    def cleanup(self) -> None:
        now = time.time()
        for folder in self.root.iterdir():
            if not (folder.is_dir() and _ID.match(folder.name)):
                continue
            try:
                created = json.loads((folder / "meta.json").read_text(encoding="utf-8")).get("created", 0)
            except (OSError, ValueError):
                created = folder.stat().st_mtime if folder.exists() else now
            if now - created > self.keep_seconds:
                shutil.rmtree(folder, ignore_errors=True)

    async def add(
        self, file_id: str, folder: Path, name: str, data: bytes, sections: list[tuple[str, str]], *,
        truncated: bool = False, pages: int | None = None, tables: list[dict[str, Any]] | None = None, query: bool = False,
    ) -> ChatFile:
        """Indexes a read file. `sections` are (label, text): a PDF's pages, or one for the whole file."""
        text_chars = sum(len(t) for _, t in sections)
        found = split_passages(sections, self.config.chunk_chars, self.config.overlap_chars)
        file = ChatFile(
            id=file_id, name=name, kind="table" if tables else "text", chars=text_chars,
            tokens=sum(estimate(t) for _, t in sections), pages=pages, truncated=truncated, created=time.time(),
            query=query, tables=tables or [], folder=folder,
        )
        (folder / "sections.json").write_text(json.dumps(sections, ensure_ascii=False), encoding="utf-8")
        if found:
            index = Workspace(folder)
            sha = hashlib.sha256(data).hexdigest()
            await asyncio.to_thread(index.add, name, sha, text_chars, pages, truncated, found, [None] * len(found))
            if (await self.embedding.status())["ready"] and not query:
                file.vectors = "pending"
                task = asyncio.create_task(self._embed(file))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        self._save(file)
        log.info("Chat files: %s kept as %s (%d passages, %s tokens)", name, file_id, len(found), file.tokens)
        return file

    def _save(self, file: ChatFile) -> None:
        data = asdict(file)
        data.pop("folder")
        (file.folder / "meta.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def get(self, file_id: str) -> ChatFile | None:
        """The file, or None when it is not (or no longer) kept here."""
        if not _ID.match(file_id or ""):
            return None
        folder = self.root / file_id
        try:
            data = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if time.time() - data.get("created", 0) > self.keep_seconds:
            return None
        return ChatFile(**data, folder=folder)

    def sections(self, file: ChatFile) -> list[tuple[str, str]]:
        return [tuple(s) for s in json.loads((file.folder / "sections.json").read_text(encoding="utf-8"))]

    def text(self, file: ChatFile) -> str:
        return "\n\n".join(f"[{label}]\n{text}" if label else text for label, text in self.sections(file))

    async def _embed(self, file: ChatFile) -> None:
        """Vectors for every passage, after the upload has answered. Words work meanwhile."""
        index = Workspace(file.folder)
        try:
            todo = await asyncio.to_thread(index.passages_without_vectors)
            for start in range(0, len(todo), 64):
                batch = todo[start:start + 64]
                vectors = await self.embedding.documents([text for _, text in batch])
                await asyncio.to_thread(index.set_vectors, [(i, v) for (i, _), v in zip(batch, vectors)])
            file.vectors = "ready"
        except PipelineError as exc:
            log.info("Chat files: no vectors for %s (%s); search by words", file.name, exc)
            self.embedding.forget()
            file.vectors = "failed"
        if (file.folder / "meta.json").exists():
            self._save(file)

    # ── what a question gets ──
    async def context(
        self, files: list[ChatFile], question: str, max_tokens: int, start: int = 1,
    ) -> tuple[str, dict[str, Any] | None]:
        """The text the model gets with the question, and a `sources` event (None when files are given whole).

        `files` are the conversation's text files, the newest turn's first.
        """
        if not files:
            return "", None
        texts = {f.id: self.text(f) for f in files}
        sizes = {f.id: estimate(texts[f.id]) + 24 for f in files}
        if sum(sizes.values()) <= max_tokens:
            whole = "\n\n".join(f'<file name="{f.name}">\n{texts[f.id]}\n</file>' for f in files)
            return prompts.FILES_WHOLE.format(files=whole), None

        # Small files whole, the rest as passages, as many as fit.
        whole, rest, used = [], [], 0
        for f in files:
            if sizes[f.id] <= max_tokens * 0.25 and used + sizes[f.id] <= max_tokens * 0.5:
                whole.append(f)
                used += sizes[f.id]
            else:
                rest.append(f)
        vector = await self.embedding.question(question) if any(f.vectors == "ready" for f in rest) else None
        ranked: list[list[Hit]] = []
        for f in rest:
            hits = await asyncio.to_thread(Workspace(f.folder).search, question, vector if f.vectors == "ready" else None, 40)
            if not hits:  # nothing matched the words: the start of the file is better than nothing
                start_of_file = ((lab, part) for lab, t in self.sections(f)[:8] for part in split(t[:20_000], self.config.chunk_chars, 0))
                hits = [Hit(0, f.name, label, i, text, 0.0, "start") for i, (label, text) in enumerate(islice(start_of_file, 8))]
            ranked.append(hits)
        chosen: list[Hit] = []
        for depth in range(max((len(r) for r in ranked), default=0)):  # one from each file in turn, best first
            for hits in ranked:
                if depth < len(hits):
                    cost = estimate(hits[depth].text) + 16
                    if used + cost <= max_tokens:
                        chosen.append(hits[depth])
                        used += cost
        listed = "\n\n".join(f"[{i}] {prompts.where(h)}\n{h.text}" for i, h in enumerate(chosen, start))
        whole_note = (" These are given whole: " + ", ".join(f.name for f in whole) + ".") if whole else ""
        block = prompts.FILES_PASSAGES.format(whole=whole_note, passages=listed)
        if whole:
            block = "\n\n".join(f'<file name="{f.name}">\n{texts[f.id]}\n</file>' for f in whole) + "\n\n" + block
        items = [{"n": i, "document": h.document, "label": h.label, "matched_by": h.matched_by,
                  "text": h.text if len(h.text) <= SNIPPET else h.text[:SNIPPET].rstrip() + " …"}
                 for i, h in enumerate(chosen, start)]
        mode = "meaning and words" if vector is not None else "words"
        return block, {"type": "sources", "workspace": "the attached files", "mode": mode, "items": items}

    # ── reading a whole file, for a summary ──
    def parts(self, file: ChatFile, part_tokens: int) -> list[tuple[str, str]]:
        """The file in parts of about `part_tokens`, each labelled with its pages when it has them."""
        out: list[tuple[str, str]] = []
        size = max(2000, int(part_tokens * 3.2))  # characters: the token estimate leans high, so this fits
        text, labels = "", []
        for section_label, section_text in self.sections(file):
            for piece in split(section_text, size, 0):
                if text and len(text) + len(piece) > size:
                    out.append((_span(labels), text))
                    text, labels = "", []
                text = f"{text}\n\n{piece}" if text else piece
                if section_label:
                    labels.append(section_label)
        if text:
            out.append((_span(labels), text))
        return out

    async def read_whole(
        self, file: ChatFile, entry: ModelEntry, part_tokens: int, notes_tokens: int,
    ) -> AsyncIterator[dict[str, Any]]:
        """Reads the whole file in parts with the chat's model. Yields `progress` events, then `notes`.

        The notes of each part are kept with the file, per model and part size,
        so a second question about the same file does not read it again.
        """
        parts = self.parts(file, part_tokens)
        skipped = max(0, len(parts) - MAX_PARTS)
        parts = parts[:MAX_PARTS]
        cache = file.folder / f"notes-{hashlib.sha1(f'{entry.model}:{part_tokens}'.encode()).hexdigest()[:12]}.json"
        try:
            notes: dict[str, str] = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            notes = {}
        todo = [i for i in range(len(parts)) if str(i) not in notes]
        done = len(parts) - len(todo)
        yield {"type": "progress", "done": done, "total": len(parts)}

        slots = asyncio.Semaphore(CONCURRENT_PARTS)

        async def one(i: int) -> tuple[int, str]:
            async with slots:
                return i, await self._note(entry, file, i, len(parts), parts[i], part_tokens)

        tasks = [asyncio.create_task(one(i)) for i in todo]
        try:
            for finished in asyncio.as_completed(tasks):
                i, note = await finished
                notes[str(i)] = note
                done += 1
                cache.write_text(json.dumps(notes, ensure_ascii=False), encoding="utf-8")
                yield {"type": "progress", "done": done, "total": len(parts)}
        finally:
            for task in tasks:
                task.cancel()

        lines = [f"Part {i + 1}{f' ({parts[i][0]})' if parts[i][0] else ''}:\n{notes[str(i)]}" for i in range(len(parts))]
        lines = await self._combine(entry, file, lines, notes_tokens, part_tokens)
        more = f", the first {MAX_PARTS} of {MAX_PARTS + skipped}" if skipped else ""
        text = prompts.FILE_NOTES.format(name=file.name, parts=len(parts), s="s" if len(parts) != 1 else "",
                                         skipped=more, notes="\n\n".join(lines))
        yield {"type": "notes", "text": text, "parts": len(parts), "skipped": skipped}

    async def _ask(self, entry: ModelEntry, system: str, user: str) -> str:
        options = {**entry.options, "temperature": 0.2, "num_predict": NOTE_TOKENS}
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        out = ""
        async for chunk in self.registry.client(entry.host).chat_stream(entry.model, messages, options):
            out += (chunk.get("message") or {}).get("content") or ""
        return out.strip()

    async def _note(self, entry: ModelEntry, file: ChatFile, i: int, total: int, part: tuple[str, str], part_tokens: int) -> str:
        where = f" ({part[0]})" if part[0] else ""
        text = part[1]
        for _ in range(3):
            try:
                return await self._ask(entry, prompts.NOTES_SYSTEM,
                                       prompts.NOTES.format(part=i + 1, parts=total, name=file.name, where=where, text=text))
            except ContextOverflow:
                text = cut_middle(text, int(estimate(text) * 0.7))  # the estimate fell short: a little less
        return "(this part could not be read)"

    async def _combine(self, entry: ModelEntry, file: ChatFile, lines: list[str], limit: int, part_tokens: int) -> list[str]:
        """Notes that are together too long are combined, in groups, until they fit `limit`."""
        for _ in range(3):
            if sum(estimate(line) for line in lines) <= limit or len(lines) <= 1:
                return lines
            groups, group, size = [], [], 0
            for line in lines:
                cost = estimate(line)
                if group and size + cost > part_tokens:
                    groups.append(group)
                    group, size = [], 0
                group.append(line)
                size += cost
            if group:
                groups.append(group)
            lines = [await self._ask(entry, prompts.NOTES_SYSTEM,
                                     prompts.COMBINE.format(name=file.name, notes="\n\n".join(g))) for g in groups]
        return [cut_middle("\n\n".join(lines), limit)]


def _span(labels: list[str]) -> str:
    """"pages 3-7" from ["page 3", ..., "page 7"]."""
    if not labels:
        return ""
    numbers = [label.removeprefix("page ") for label in labels]
    if all(n.isdigit() for n in numbers):
        first, last = numbers[0], numbers[-1]
        return f"page {first}" if first == last else f"pages {first}-{last}"
    return labels[0] if len(set(labels)) == 1 else f"{labels[0]} to {labels[-1]}"
