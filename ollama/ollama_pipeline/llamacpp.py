"""Client for a llama.cpp `llama-server`, with the same methods as `OllamaClient`.

A host in models.yaml with `kind: llamacpp` gets this client instead of the
Ollama one, so the pipeline, the documents pages and the SQL tools work the
same on either. It talks to llama-server's OpenAI-compatible API
(`/v1/chat/completions`) and turns its answers into the chunks Ollama's
`/api/chat` gives: `message.content`, `message.thinking`, `message.tool_calls`,
and a last chunk with `done` and the timings.

What llama-server needs to be started with:
- `--jinja` for tool calls (the SQL connector) and for thinking models;
- `--mmproj <file>` for a model to read pictures;
- `-c <tokens>` for the context size: it is the server's, `num_ctx` in
  models.yaml cannot change it per request;
- `--alias <name>` is optional: a server that serves one model answers to
  any name, see `Registry`.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .client import JSON_TIMEOUT, STREAM_TIMEOUT
from .errors import ContextOverflow, OllamaError, OllamaUnavailable

KIND = "llamacpp"
PROPS_SECONDS = 60
DEFAULT_PORT = 8080

# Ollama's option names -> the OpenAI-style fields llama-server reads. Anything
# else (num_ctx, num_gpu, ...) is a setting of the server itself and is left out.
_OPTIONS = {
    "temperature": "temperature",
    "top_p": "top_p",
    "top_k": "top_k",
    "min_p": "min_p",
    "seed": "seed",
    "stop": "stop",
    "num_predict": "max_tokens",
    "repeat_penalty": "repeat_penalty",
    "presence_penalty": "presence_penalty",
    "frequency_penalty": "frequency_penalty",
}


def _explain(text: str) -> str:
    """llama-server's own error, with what to do about it when that is known."""
    if "jinja" in text.lower():
        return f"{text}. Start this llama-server with --jinja so its model can call tools."
    if "mmproj" in text.lower() or "image input is not supported" in text.lower():
        return f"{text}. Start this llama-server with --mmproj so its model can read pictures."
    return text


def _error(response: httpx.Response) -> OllamaError:
    """The error llama-server answered with. A request too big for its context gets its own
    class, with the server's counts, so the caller can make it fit and try again."""
    try:
        data = response.json()
    except ValueError:
        data = {}
    error = data.get("error") if isinstance(data, dict) else None
    error = error if isinstance(error, dict) else {}
    message = _error_text(response)
    if error.get("type") == "exceed_context_size_error" or "context size" in message.lower():
        return ContextOverflow(message, error.get("n_prompt_tokens"), error.get("n_ctx"))
    return OllamaError(message)


def _error_text(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return _explain(response.text or f"llama-server returned HTTP {response.status_code}")
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        return _explain(error.get("message") or json.dumps(error))
    return _explain(str(error or response.text))


def _messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ollama-style messages -> OpenAI-style ones.

    Pictures become `image_url` parts, tool calls get ids and JSON-string
    arguments, and a tool's answer is tied back to the call it answers.
    """
    out: list[dict[str, Any]] = []
    pending: list[str] = []  # ids of the calls not answered yet, in order
    for i, message in enumerate(messages):
        item: dict[str, Any] = {"role": message["role"], "content": message.get("content") or ""}
        if message.get("images"):
            item["content"] = [
                *([{"type": "text", "text": item["content"]}] if item["content"] else []),
                *({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image}"}} for image in message["images"]),
            ]
        if message.get("tool_calls"):
            calls = []
            for j, call in enumerate(message["tool_calls"]):
                function = call.get("function") or {}
                arguments = function.get("arguments")
                call_id = call.get("id") or f"call_{i}_{j}"
                calls.append({
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": function.get("name") or "",
                        "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments or {}),
                    },
                })
                pending.append(call_id)
            item["tool_calls"] = calls
        if message["role"] == "tool":
            item["tool_call_id"] = message.get("tool_call_id") or (pending.pop(0) if pending else f"call_{i}")
            if message.get("tool_call_id") in pending:
                pending.remove(message["tool_call_id"])
        out.append(item)
    return out


def _arguments(text: str) -> Any:
    try:
        return json.loads(text) if text.strip() else {}
    except ValueError:
        return text  # passed on as it is; the tool says what is wrong with it


class LlamaCppClient:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self._http = httpx.AsyncClient(base_url=base_url, timeout=10.0)
        self._props: dict[str, Any] | None = None
        self._props_at = 0.0

    def _unavailable(self, exc: Exception) -> OllamaUnavailable:
        return OllamaUnavailable(
            f"Can't reach llama-server at {self.base_url} ({type(exc).__name__}). Is `llama-server` running?"
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise self._unavailable(exc) from exc
        if response.status_code >= 400:
            raise _error(response)
        return response.json()

    async def _server_props(self) -> dict[str, Any]:
        # Read again after a minute: the server may have been restarted with other settings.
        if self._props is None or time.monotonic() - self._props_at > PROPS_SECONDS:
            self._props = await self._request("GET", "/props")
            self._props_at = time.monotonic()
        return self._props

    async def context(self) -> tuple[int | None, int | None]:
        """(tokens one request may hold, number of slots), from the server's own settings.

        One request holds one slot's context: `-c` divided by `-np`, or all of
        `-c` when the server was started with --kv-unified.
        """
        props = await self._server_props()
        n_ctx = (props.get("default_generation_settings") or {}).get("n_ctx")
        return (int(n_ctx) if n_ctx else None), props.get("total_slots")

    async def version(self) -> str:
        props = await self._request("GET", "/props")
        self._props, self._props_at = props, time.monotonic()
        return f"llama.cpp {props.get('build_info') or ''}".strip()

    async def list_models(self) -> list[dict[str, Any]]:
        """The models this server serves, in the shape of Ollama's /api/tags."""
        data = await self._request("GET", "/v1/models")
        return [
            {"name": m["id"], "digest": m["id"], "size": None, "details": {}}
            for m in data.get("data") or []
            if m.get("id")
        ]

    async def show(self, model: str) -> dict[str, Any]:
        """Ollama's /api/show capabilities, worked out from what /props says.

        `vision` when the server was started with a picture projector,
        `tools` when its chat template knows tools (needs `--jinja` to work).
        A model entry in models.yaml can say `tools:` or `vision:` to overrule this.
        """
        props = await self._server_props()
        caps = ["completion"]
        if (props.get("modalities") or {}).get("vision"):
            caps.append("vision")
        template = props.get("chat_template") or ""
        if "tools" in template or "tool_call" in template:
            caps.append("tools")
        if "<think>" in template or "enable_thinking" in template or "reasoning" in template:
            caps.append("thinking")
        return {"capabilities": caps}

    def _payload(
        self, model: str, messages: list[dict[str, Any]], options: dict[str, Any] | None, stream: bool
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": model, "messages": _messages(messages), "stream": stream}
        for name, value in (options or {}).items():
            if name in _OPTIONS:
                payload[_OPTIONS[name]] = value
        return payload

    async def chat_stream(
        self, model: str, messages: list[dict[str, Any]], options: dict[str, Any] | None = None,
        *, tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yields chunks shaped like Ollama's /api/chat ones as they arrive."""
        payload = self._payload(model, messages, options, stream=True)
        if tools:
            payload["tools"] = tools
        calls: dict[int, dict[str, Any]] = {}  # tool calls arrive in pieces, keyed by their index
        timings: dict[str, Any] = {}
        try:
            async with self._http.stream(
                "POST", "/v1/chat/completions", json=payload, timeout=STREAM_TIMEOUT
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise _error(response)
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    if chunk.get("error"):
                        err = chunk["error"]
                        if isinstance(err, dict) and err.get("type") == "exceed_context_size_error":
                            raise ContextOverflow(_error_text_from(err), err.get("n_prompt_tokens"), err.get("n_ctx"))
                        raise OllamaError(_error_text_from(err))
                    timings = chunk.get("timings") or timings
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        message: dict[str, Any] = {"role": "assistant"}
                        if delta.get("reasoning_content"):
                            message["thinking"] = delta["reasoning_content"]
                        if delta.get("content"):
                            message["content"] = delta["content"]
                        for piece in delta.get("tool_calls") or []:
                            call = calls.setdefault(piece.get("index", len(calls)), {"id": "", "name": "", "arguments": ""})
                            call["id"] = piece.get("id") or call["id"]
                            function = piece.get("function") or {}
                            call["name"] += function.get("name") or ""
                            call["arguments"] += function.get("arguments") or ""
                        if len(message) > 1:
                            yield {"model": model, "message": message, "done": False}
        except httpx.HTTPError as exc:
            raise self._unavailable(exc) from exc

        if calls:
            yield {
                "model": model,
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": c["id"], "function": {"name": c["name"], "arguments": _arguments(c["arguments"])}}
                        for _, c in sorted(calls.items())
                    ],
                },
                "done": False,
            }
        yield {"model": model, "message": {"role": "assistant", "content": ""}, "done": True, **_stats(timings)}

    async def chat_json(
        self, model: str, messages: list[dict[str, Any]], schema: dict[str, Any], options: dict[str, Any] | None = None,
        *, think: bool | None = None,
    ) -> Any:
        """One answer, held by llama-server to the given JSON schema (it turns it into a grammar)."""
        payload = self._payload(model, messages, options, stream=False)
        payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}}
        if think is False:
            # Templates with a thinking switch (Qwen 3 and others) read this; the rest ignore it.
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        data = await self._request("POST", "/v1/chat/completions", json=payload, timeout=JSON_TIMEOUT)
        content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        try:
            return json.loads(content)
        except ValueError:
            found = re.search(r"\{.*\}", content, re.DOTALL)
            try:
                return json.loads(found.group(0)) if found else None
            except ValueError:
                return None

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        """One vector per text. Needs a llama-server started with `--embedding` and an embedding model."""
        data = await self._request("POST", "/v1/embeddings", json={"model": model, "input": texts}, timeout=JSON_TIMEOUT)
        items = sorted(data.get("data") or [], key=lambda item: item.get("index", 0))
        vectors = [item.get("embedding") or [] for item in items]
        if len(vectors) != len(texts):
            raise OllamaError(f"llama-server returned {len(vectors)} vectors for {len(texts)} texts. Was it started with --embedding?")
        return vectors

    async def aclose(self) -> None:
        await self._http.aclose()


def _error_text_from(error: Any) -> str:
    return _explain(error.get("message") or json.dumps(error) if isinstance(error, dict) else str(error))


def _stats(timings: dict[str, Any]) -> dict[str, Any]:
    """llama-server's timings in Ollama's names (durations in nanoseconds)."""
    if not timings:
        return {}
    prompt_ms, predicted_ms = timings.get("prompt_ms") or 0, timings.get("predicted_ms") or 0
    return {
        "prompt_eval_count": timings.get("prompt_n"),
        "eval_count": timings.get("predicted_n"),
        "eval_duration": int(predicted_ms * 1e6) or None,
        "total_duration": int((prompt_ms + predicted_ms) * 1e6) or None,
    }
