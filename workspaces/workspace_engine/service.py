"""Workspaces at work: adding documents, searching them, and the context a chat gets.

The model's only part here is the embedding model, which turns passages and
questions into vectors. Without it everything still works, by keywords.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import numpy as np
from ollama_pipeline import PipelineError, Registry, estimate

from . import prompts
from .chunking import passages as split_passages
from .embedding import BATCH, Embedder
from .index import Hit, Workspace, WorkspaceError, Workspaces

log = logging.getLogger(__name__)

SNIPPET = 600  # characters of a passage shown under an answer


class WorkspaceService:
    def __init__(self, registry: Registry, root: Path, embedding: Embedder | None = None):
        self.registry = registry
        self.config = registry.config.workspaces
        self.store = Workspaces(root)
        self.embedding = embedding or Embedder(registry)

    # ── the embedding model ──
    async def _embed(self, texts: list[str], prefix: str) -> list[np.ndarray]:
        return await self.embedding.embed(texts, prefix)

    async def embedder(self, fresh: bool = False) -> dict[str, Any]:
        """Whether passages can be searched by meaning right now, and if not, why."""
        return await self.embedding.status(fresh)

    def _stale(self, workspace: Workspace) -> bool:
        """Vectors made by another embedding model than the one set now cannot be compared with new ones."""
        made_by = workspace.meta().get("embed_model") or ""
        return bool(made_by) and made_by != self.config.embed_model

    # ── workspaces ──
    def summary(self, workspace: Workspace) -> dict[str, Any]:
        meta, counts = workspace.meta(), workspace.counts()
        return {**meta, **counts, "stale": self._stale(workspace),
                "missing_vectors": counts["passages"] - counts["with_vectors"]}

    def list(self) -> list[dict[str, Any]]:
        return sorted((self.summary(w) for w in self.store.all()), key=lambda w: w["name"].lower())

    def create(self, name: str, instructions: str = "") -> dict[str, Any]:
        return self.summary(self.store.create(name, instructions))

    def update(self, workspace_id: str, name: str | None, instructions: str | None) -> dict[str, Any]:
        workspace = self.store.get(workspace_id)
        changes: dict[str, Any] = {}
        if name is not None and name.strip():
            if any(w.id != workspace_id and w.meta()["name"].lower() == name.strip().lower() for w in self.store.all()):
                raise WorkspaceError(f"A workspace named '{name.strip()}' already exists.")
            changes["name"] = name.strip()[:80]
        if instructions is not None:
            changes["instructions"] = instructions.strip()[:4000]
        workspace.save_meta(**changes)
        return self.summary(workspace)

    def delete(self, workspace_id: str) -> None:
        self.store.delete(workspace_id)

    # ── documents ──
    async def add(
        self, workspace_id: str, name: str, data: bytes, sections: list[tuple[str, str]], truncated: bool,
        pages: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Splits a read document into passages, gives them vectors when it can, and stores them.

        Yields progress events, then `added` (or `skipped` when the same file is already there).
        """
        workspace = self.store.get(workspace_id)
        sha = hashlib.sha256(data).hexdigest()
        existing, same = workspace.find(name, sha)
        if same:
            yield {"type": "skipped", "name": name, "message": f"{name} is already in this workspace, unchanged."}
            return

        yield {"type": "progress", "name": name, "message": "Splitting into passages"}
        found = split_passages(sections, self.config.chunk_chars, self.config.overlap_chars)
        if not found:
            raise WorkspaceError(f"{name} has no text to search.")

        vectors: list[np.ndarray | None] = [None] * len(found)
        note = None
        status = await self.embedder()
        if status["ready"] and self._stale(workspace):
            note = "Search by meaning is paused for this workspace until it is re-indexed with the new embedding model."
        elif status["ready"]:
            try:
                for start in range(0, len(found), BATCH):
                    yield {"type": "progress", "name": name,
                           "message": f"Reading for meaning: {min(start + BATCH, len(found))} of {len(found)} passages"}
                    batch = await self._embed([p.text for p in found[start:start + BATCH]], self.config.document_prefix)
                    vectors[start:start + len(batch)] = batch
                if not workspace.meta().get("embed_model"):
                    workspace.save_meta(embed_model=self.config.embed_model)
            except PipelineError as exc:
                vectors = [None] * len(found)
                self.embedding.forget()
                note = f"Indexed for keyword search only: the embedding model failed ({exc}). Re-index later."
        else:
            note = status["reason"]

        document_id = await asyncio.to_thread(
            workspace.add, name, sha, sum(len(t) for _, t in sections), pages, truncated, found, vectors
        )
        log.info("Workspaces: %s added to %s (%d passages, vectors: %s)", name, workspace_id, len(found), vectors[0] is not None)
        yield {
            "type": "added", "name": name, "id": document_id, "passages": len(found), "replaced": existing is not None,
            "by_meaning": vectors[0] is not None, "truncated": truncated, "note": note,
        }

    def remove(self, workspace_id: str, document_id: int) -> bool:
        return self.store.get(workspace_id).remove(document_id)

    async def reindex(self, workspace_id: str) -> AsyncIterator[dict[str, Any]]:
        """Gives vectors to every passage that has none; after a change of embedding model, to all of them."""
        workspace = self.store.get(workspace_id)
        status = await self.embedder(fresh=True)
        if not status["ready"]:
            raise WorkspaceError(status["reason"])
        if self._stale(workspace):
            workspace.clear_vectors()
        workspace.save_meta(embed_model=self.config.embed_model)
        todo = workspace.passages_without_vectors()
        for start in range(0, len(todo), BATCH):
            batch = todo[start:start + BATCH]
            yield {"type": "progress", "message": f"Reading for meaning: {start + len(batch)} of {len(todo)} passages"}
            vectors = await self._embed([text for _, text in batch], self.config.document_prefix)
            await asyncio.to_thread(workspace.set_vectors, [(chunk_id, v) for (chunk_id, _), v in zip(batch, vectors)])
        yield {"type": "done", **self.summary(workspace)}

    # ── search ──
    async def search(self, workspace_id: str, question: str, top_k: int | None = None) -> tuple[list[Hit], str]:
        """The passages that best match a question, and how they were found ("meaning and words" or "words")."""
        workspace = self.store.get(workspace_id)
        vector = None
        if not self._stale(workspace) and workspace.counts()["with_vectors"]:
            vector = await self.embedding.question(question)
        hits = await asyncio.to_thread(workspace.search, question, vector, top_k or self.config.top_k)
        return hits, "meaning and words" if vector is not None else "words"

    @staticmethod
    def question(messages: list[dict[str, Any]]) -> str:
        """What to search for: the last question, with the one before when it is a short follow-up."""
        asked = [m.get("content") or "" for m in messages if m.get("role") == "user"]
        if not asked:
            return ""
        last = asked[-1]
        if len(last.split()) < 6 and len(asked) > 1:
            return asked[-2] + "\n" + last
        return last

    async def context(
        self, workspace_id: str, messages: list[dict[str, Any]], max_tokens: int | None = None, start: int = 1,
    ) -> tuple[str, str, dict[str, Any] | None]:
        """What a chat reply gets from the workspace: (rules, passages, a `sources` event).

        The rules go in the system message, where they stay the same from turn
        to turn (llama-server reuses that part); the passages go with the
        question, numbered from `start`, at most `max_tokens` of them. No
        passages and no event when the last message has no text to search for.
        """
        workspace = self.store.get(workspace_id)
        meta = workspace.meta()
        rules = prompts.rules(meta["name"], meta.get("instructions") or "")
        question = self.question(messages)
        if not question.strip():
            return rules, "", None  # a message with only a picture: nothing to search for
        hits, mode = await self.search(workspace_id, question)
        if max_tokens:
            kept, used = [], 0
            for hit in hits:
                cost = estimate(hit.text) + 16
                if used + cost > max_tokens and kept:
                    break
                kept.append(hit)
                used += cost
            hits = kept
        items = [
            {"n": i, "document": h.document, "label": h.label, "matched_by": h.matched_by,
             "text": h.text if len(h.text) <= SNIPPET else h.text[:SNIPPET].rstrip() + " …"}
            for i, h in enumerate(hits, start)
        ]
        event = {"type": "sources", "workspace": meta["name"], "mode": mode, "items": items}
        return rules, prompts.passages(meta["name"], hits, start), event
