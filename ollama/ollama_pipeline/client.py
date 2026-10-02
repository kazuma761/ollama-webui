"""Thin async client for one Ollama server's REST API."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .errors import OllamaError, OllamaUnavailable

# A cold model can take a long time to load before the first token arrives.
STREAM_TIMEOUT = httpx.Timeout(10.0, read=None)


def _error_text(response: httpx.Response) -> str:
    try:
        return response.json().get("error") or response.text
    except ValueError:
        return response.text or f"Ollama returned HTTP {response.status_code}"


class OllamaClient:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self._http = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    def _unavailable(self, exc: Exception) -> OllamaUnavailable:
        return OllamaUnavailable(f"Can't reach Ollama at {self.base_url} ({type(exc).__name__}). Is `ollama serve` running?")

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise self._unavailable(exc) from exc
        if response.status_code >= 400:
            raise OllamaError(_error_text(response))
        return response.json()

    async def version(self) -> str:
        return (await self._request("GET", "/api/version")).get("version", "")

    async def list_models(self) -> list[dict[str, Any]]:
        """Models pulled on this server (`ollama list`)."""
        return (await self._request("GET", "/api/tags")).get("models") or []

    async def show(self, model: str) -> dict[str, Any]:
        return await self._request("POST", "/api/show", json={"model": model})

    async def chat_stream(
        self, model: str, messages: list[dict[str, Any]], options: dict[str, Any] | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Yields Ollama's raw /api/chat chunks as they arrive."""
        payload: dict[str, Any] = {"model": model, "messages": messages, "stream": True}
        if options:
            payload["options"] = options
        try:
            async with self._http.stream("POST", "/api/chat", json=payload, timeout=STREAM_TIMEOUT) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise OllamaError(_error_text(response))
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    if chunk.get("error"):
                        raise OllamaError(chunk["error"])
                    yield chunk
        except httpx.HTTPError as exc:
            raise self._unavailable(exc) from exc

    async def aclose(self) -> None:
        await self._http.aclose()
