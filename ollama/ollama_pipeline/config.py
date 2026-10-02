"""Loads hosts, model aliases and themes from ollama/config/*.yaml."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_URL = "http://127.0.0.1:11434"


@dataclass(frozen=True)
class ModelAlias:
    id: str
    model: str
    host: str
    label: str
    description: str = ""
    system: str = ""
    options: dict[str, Any] = field(default_factory=dict)
    vision: bool | None = None  # None = ask Ollama


@dataclass(frozen=True)
class Theme:
    title: str
    prompt: str


@dataclass(frozen=True)
class PipelineConfig:
    hosts: dict[str, str]
    default_host: str
    default_model: str | None
    discover: bool
    models: list[ModelAlias]
    themes: list[Theme]


def config_dir() -> Path:
    override = os.environ.get("OLLAMA_PIPELINE_CONFIG")
    return Path(override) if override else Path(__file__).resolve().parent.parent / "config"


def _normalize_url(url: str) -> str:
    """Accepts the forms OLLAMA_HOST allows: host, host:port or a full URL."""
    url = url.strip().rstrip("/")
    if "://" not in url:
        url = f"http://{url}"
    if not re.search(r":\d+$", url):
        url = f"{url}:11434"
    return url


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_config(directory: Path | None = None) -> PipelineConfig:
    directory = directory or config_dir()
    raw = _read(directory / "models.yaml")

    hosts = {name: _normalize_url(url) for name, url in (raw.get("hosts") or {}).items()}
    default_host = raw.get("default_host") or next(iter(hosts), "local")
    hosts.setdefault(default_host, DEFAULT_URL)

    for name in hosts:
        override = os.environ.get(f"OLLAMA_HOST_{re.sub(r'[^A-Za-z0-9]', '_', name).upper()}")
        if override:
            hosts[name] = _normalize_url(override)
    if os.environ.get("OLLAMA_HOST"):
        hosts[default_host] = _normalize_url(os.environ["OLLAMA_HOST"])

    models = []
    for item in raw.get("models") or []:
        host = item.get("host") or default_host
        if host not in hosts:
            raise ValueError(f"models.yaml: model '{item.get('id')}' uses unknown host '{host}'")
        models.append(
            ModelAlias(
                id=item["id"],
                model=item["model"],
                host=host,
                label=item.get("label") or item["id"],
                description=item.get("description") or "",
                system=(item.get("system") or "").strip(),
                options=item.get("options") or {},
                vision=item.get("vision"),
            )
        )

    themes = [Theme(t["title"], t["prompt"]) for t in _read(directory / "themes.yaml").get("themes") or []]

    return PipelineConfig(
        hosts=hosts,
        default_host=default_host,
        default_model=raw.get("default_model"),
        discover=bool(raw.get("discover", True)),
        models=models,
        themes=themes,
    )
