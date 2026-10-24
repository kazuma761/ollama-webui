"""The embedding model: text -> vectors, for search by meaning. Shared by workspaces and chat files.

Optional. Without it (not set, not running, or failing) search is by words,
and nothing else changes. Its state is checked at most once a minute.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np
from ollama_pipeline import OllamaError, PipelineError, Registry

BATCH = 16  # passages sent to the embedding model at once
STATUS_SECONDS = 60  # how long a check of the embedding model is trusted


def unit(vector: list[float]) -> np.ndarray:
    array = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(array))
    return array / norm if norm else array


class Embedder:
    def __init__(self, registry: Registry):
        self.registry = registry
        self.config = registry.config.workspaces
        self._status: tuple[float, dict[str, Any]] | None = None

    @property
    def model(self) -> str:
        return self.config.embed_model

    async def embed(self, texts: list[str], prefix: str) -> list[np.ndarray]:
        client = self.registry.client(self.config.embed_host)
        vectors = await client.embed(self.config.embed_model, [prefix + t for t in texts])
        return [unit(v) for v in vectors]

    async def documents(self, texts: list[str]) -> list[np.ndarray]:
        out: list[np.ndarray] = []
        for start in range(0, len(texts), BATCH):
            out += await self.embed(texts[start:start + BATCH], self.config.document_prefix)
        return out

    async def question(self, text: str) -> np.ndarray | None:
        """The vector of a question, or None when the embedding model cannot give one now."""
        if not text.strip() or not (await self.status())["ready"]:
            return None
        try:
            return (await self.embed([text], self.config.query_prefix))[0]
        except (OllamaError, PipelineError):
            self._status = None
            return None

    async def status(self, fresh: bool = False) -> dict[str, Any]:
        """Whether passages can be searched by meaning right now, and if not, why."""
        if not self.config.embed_model:
            return {"ready": False, "model": "", "reason": "No embedding model is set (workspaces.embed_model): search is by keywords."}
        if self._status and not fresh and time.monotonic() - self._status[0] < STATUS_SECONDS:
            return self._status[1]
        try:
            await self.embed(["ready?"], self.config.query_prefix)
            status = {"ready": True, "model": self.config.embed_model, "reason": ""}
        except PipelineError as exc:
            status = {"ready": False, "model": self.config.embed_model,
                      "reason": f"The embedding model {self.config.embed_model} is not reachable ({exc}). Search is by keywords until it is."}
        self._status = (time.monotonic(), status)
        return status

    def forget(self) -> None:
        """Check again next time: a call failed."""
        self._status = None
