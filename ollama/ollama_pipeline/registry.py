"""Model registry: merges configured aliases with what each Ollama host has pulled."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from typing import Any

from .client import OllamaClient
from .llamacpp import KIND as LLAMACPP, LlamaCppClient
from .config import PipelineConfig
from .errors import ModelNotFound, OllamaError, OllamaUnavailable
from .opencode import PROVIDER as OPENCODE, OpenCodeClient


@dataclass
class ModelEntry:
    id: str
    label: str
    model: str
    host: str
    description: str = ""
    available: bool = False
    vision: bool | None = None  # None = the server did not report capabilities
    thinking: bool = False  # the model reasons before it answers (Ollama capability "thinking")
    tools: bool | None = None  # the model can call tools (Ollama capability "tools"); None = not reported
    context: int | None = None  # tokens one request may hold: llama-server's slot size, or num_ctx for Ollama
    configured: bool = False
    size: int | None = None
    parameter_size: str | None = None
    system: str = ""
    options: dict[str, Any] = field(default_factory=dict)
    provider: str = "ollama"  # "opencode" = answered in the cloud, not on this machine

    @property
    def budget(self) -> int:
        """Tokens one request may hold, with a careful default when the server does not say."""
        return int(self.context or self.options.get("num_ctx") or 4096)

    @property
    def local(self) -> bool:
        """Answered on this machine (Ollama or llama-server), not in the cloud."""
        return self.provider != OPENCODE

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        del data["system"], data["options"]
        return data


def _find_tag(tags: list[dict[str, Any]] | None, model: str, *, only_one_served: bool = False) -> dict[str, Any] | None:
    wanted = {model, f"{model}:latest", model.removesuffix(":latest")}
    found = next((t for t in tags or [] if t["name"] in wanted), None)
    # A llama-server started for one model serves it under its file name unless given --alias:
    # an entry on such a host means that model, whatever name the entry gives it.
    if found is None and only_one_served and tags and len(tags) == 1:
        return tags[0]
    return found


class Registry:
    def __init__(self, config: PipelineConfig):
        self.config = config
        self._clients = {
            name: LlamaCppClient(url) if config.host_kinds.get(name) == LLAMACPP else OllamaClient(url)
            for name, url in config.hosts.items()
        }
        self.opencode = OpenCodeClient(config.opencode)
        self._capabilities: dict[tuple[str, str], list[str]] = {}

    def client(self, host: str) -> OllamaClient | LlamaCppClient:
        return self._clients[host]

    def kind(self, host: str) -> str:
        """"ollama", or "llamacpp" for a llama-server."""
        return self.config.host_kinds.get(host, "ollama")

    def server_name(self, host: str) -> str:
        return "llama-server" if self.kind(host) == LLAMACPP else "Ollama"

    def warm_up(self) -> None:
        """Starts anything slow to start in the background, so the first chat does not wait for it."""
        if any(alias.provider == OPENCODE for alias in self.config.models):
            self.opencode.warm_up()

    async def _installed(self, host: str) -> list[dict[str, Any]] | None:
        """Tags pulled on a host, or None when the host is unreachable."""
        try:
            return await self._clients[host].list_models()
        except OllamaError:
            return None

    async def _caps(self, host: str, tag: dict[str, Any]) -> list[str]:
        key = (host, tag.get("digest") or tag["name"])
        if key not in self._capabilities:
            try:
                info = await self._clients[host].show(tag["name"])
            except OllamaError:
                return []
            self._capabilities[key] = info.get("capabilities") or []
        return self._capabilities[key]

    async def _snapshot(self) -> tuple[list[ModelEntry], set[str]]:
        hosts = list(self._clients)
        installed = dict(zip(hosts, await asyncio.gather(*(self._installed(h) for h in hosts))))
        down = {h for h, tags in installed.items() if tags is None}

        # What one request may hold on each llama-server (its slot size); Ollama's comes from num_ctx.
        slots: dict[str, int | None] = {}
        for host in hosts:
            if host not in down and self.kind(host) == LLAMACPP:
                try:
                    slots[host] = (await self._clients[host].context())[0]
                except OllamaError:
                    slots[host] = None

        entries: list[ModelEntry] = []
        claimed: set[tuple[str, str]] = set()
        cloud_aliases = [a for a in self.config.models if a.provider == OPENCODE]
        cloud_models = await self.opencode.models() if cloud_aliases else set()

        for alias in self.config.models:
            if alias.provider == OPENCODE:
                entries.append(
                    ModelEntry(
                        id=alias.id,
                        label=alias.label,
                        model=alias.model,
                        host=alias.host,
                        description=alias.description,
                        available=alias.model in cloud_models,
                        vision=False,
                        tools=False,
                        configured=True,
                        provider=OPENCODE,
                    )
                )
                continue
            tag = _find_tag(installed[alias.host], alias.model, only_one_served=self.kind(alias.host) == LLAMACPP)
            caps = await self._caps(alias.host, tag) if tag else []
            vision = alias.vision if alias.vision is not None else ("vision" in caps if caps else None)
            tools = alias.tools if alias.tools is not None else ("tools" in caps if caps else None)
            entries.append(
                ModelEntry(
                    id=alias.id,
                    label=alias.label,
                    model=tag["name"] if tag else alias.model,
                    host=alias.host,
                    description=alias.description,
                    available=tag is not None,
                    vision=vision,
                    thinking="thinking" in caps,
                    tools=tools,
                    context=slots.get(alias.host) or (alias.options.get("num_ctx") or self.config.default_options.get("num_ctx")),
                    configured=True,
                    size=tag.get("size") if tag else None,
                    parameter_size=(tag.get("details") or {}).get("parameter_size") if tag else None,
                    system=alias.system,
                    options={**self.config.default_options, **alias.options},
                )
            )
            claimed.add((alias.host, entries[-1].model))

        if self.config.discover:
            for host, tags in installed.items():
                for tag in sorted(tags or [], key=lambda t: t["name"]):
                    if (host, tag["name"]) in claimed:
                        continue
                    caps = await self._caps(host, tag)
                    if caps and "completion" not in caps:
                        continue  # embedding models can't chat
                    entries.append(
                        ModelEntry(
                            id=f"{host}/{tag['name']}",
                            label=tag["name"],
                            model=tag["name"],
                            host=host,
                            description=f"Pulled on {host}",
                            available=True,
                            vision="vision" in caps if caps else None,
                            thinking="thinking" in caps,
                            tools="tools" in caps if caps else None,
                            context=slots.get(host) or self.config.default_options.get("num_ctx"),
                            size=tag.get("size"),
                            parameter_size=(tag.get("details") or {}).get("parameter_size"),
                            options=dict(self.config.default_options),
                        )
                    )
        return entries, down

    async def list_models(self) -> list[ModelEntry]:
        return (await self._snapshot())[0]

    def default_id(self, entries: list[ModelEntry]) -> str | None:
        available = [e.id for e in entries if e.available]
        if self.config.default_model in available:
            return self.config.default_model
        return available[0] if available else None

    async def resolve(self, model_id: str | None) -> ModelEntry:
        """Turns a picker id into a runnable model, or explains why it can't run."""
        entries, down = await self._snapshot()
        model_id = model_id or self.default_id(entries)
        entry = next((e for e in entries if e.id == model_id), None)

        if entry is None:
            if down:
                host = sorted(down)[0]
                raise OllamaUnavailable(self._down(host))
            if model_id is None:
                raise ModelNotFound("No models are available yet. Pull one first, e.g. `ollama pull llama3.2:3b`.")
            raise ModelNotFound(f"Unknown model '{model_id}'.")
        if not entry.available:
            if entry.provider == OPENCODE:
                raise ModelNotFound(
                    f"{entry.label} is not available: OpenCode is not installed here, or it no longer "
                    f"offers `{entry.model}` (see `opencode models`)."
                )
            if entry.host in down:
                raise OllamaUnavailable(self._down(entry.host))
            if self.kind(entry.host) == LLAMACPP:
                raise ModelNotFound(
                    f"{entry.label} is not served by the llama-server at {self.config.hosts[entry.host]}. "
                    f"Start it with that model (`--alias {entry.model}`), or set `model:` in models.yaml "
                    "to the name `curl {url}/v1/models` shows.".replace("{url}", self.config.hosts[entry.host])
                )
            raise ModelNotFound(
                f"{entry.label} is not pulled on host '{entry.host}'. Run `ollama pull {entry.model}`."
            )
        return entry

    def _down(self, host: str) -> str:
        if self.kind(host) == LLAMACPP:
            return f"Can't reach llama-server at {self.config.hosts[host]}. Is `llama-server` running?"
        return f"Can't reach Ollama at {self.config.hosts[host]}. Is `ollama serve` running?"

    async def health(self) -> dict[str, dict[str, Any]]:
        async def check(name: str) -> dict[str, Any]:
            url = self.config.hosts[name]
            try:
                return {"ok": True, "url": url, "version": await self._clients[name].version()}
            except OllamaError as exc:
                return {"ok": False, "url": url, "error": str(exc)}

        names = list(self._clients)
        return dict(zip(names, await asyncio.gather(*(check(n) for n in names))))

    async def aclose(self) -> None:
        await asyncio.gather(*(c.aclose() for c in self._clients.values()))
        await self.opencode.aclose()
