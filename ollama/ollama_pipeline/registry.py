"""Model registry: merges configured aliases with what each Ollama host has pulled."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from typing import Any

from .client import OllamaClient
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
    configured: bool = False
    size: int | None = None
    parameter_size: str | None = None
    system: str = ""
    options: dict[str, Any] = field(default_factory=dict)
    provider: str = "ollama"  # "opencode" = answered in the cloud, not on this machine

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        del data["system"], data["options"]
        return data


def _find_tag(tags: list[dict[str, Any]] | None, model: str) -> dict[str, Any] | None:
    wanted = {model, f"{model}:latest", model.removesuffix(":latest")}
    return next((t for t in tags or [] if t["name"] in wanted), None)


class Registry:
    def __init__(self, config: PipelineConfig):
        self.config = config
        self._clients = {name: OllamaClient(url) for name, url in config.hosts.items()}
        self.opencode = OpenCodeClient(config.opencode)
        self._capabilities: dict[tuple[str, str], list[str]] = {}

    def client(self, host: str) -> OllamaClient:
        return self._clients[host]

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
                        configured=True,
                        provider=OPENCODE,
                    )
                )
                continue
            tag = _find_tag(installed[alias.host], alias.model)
            caps = await self._caps(alias.host, tag) if tag else []
            vision = alias.vision if alias.vision is not None else ("vision" in caps if caps else None)
            entries.append(
                ModelEntry(
                    id=alias.id,
                    label=alias.label,
                    model=tag["name"] if tag else alias.model,
                    host=alias.host,
                    description=alias.description,
                    available=tag is not None,
                    vision=vision,
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
                raise OllamaUnavailable(f"Can't reach Ollama at {self.config.hosts[host]}. Is `ollama serve` running?")
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
                raise OllamaUnavailable(
                    f"Can't reach Ollama at {self.config.hosts[entry.host]}. Is `ollama serve` running?"
                )
            raise ModelNotFound(
                f"{entry.label} is not pulled on host '{entry.host}'. Run `ollama pull {entry.model}`."
            )
        return entry

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
        self.opencode.close()
