"""Loads hosts, model aliases and themes from ollama/config/*.yaml."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

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
    provider: str = "ollama"  # "ollama" (local) or "opencode" (cloud)


@dataclass(frozen=True)
class OpenCodeConfig:
    command: str = "opencode"
    agent: str = "plan"  # must be one of OpenCode's built-in agents for the free tier to answer
    timeout: int = 120  # seconds without any progress before giving up
    # Where the app's own OpenCode keeps its data, apart from any OpenCode the user runs.
    home: str = str(Path(__file__).resolve().parent.parent / ".opencode")


@dataclass(frozen=True)
class Route:
    name: str
    models: tuple[str, ...]  # ids of entries under `models:`; the first one that can answer is used
    description: str  # what the router is told this route is for

    @property
    def model(self) -> str:
        return self.models[0]


@dataclass(frozen=True)
class RouterConfig:
    enabled: bool = False
    backend: str = "von"  # "von" or "rules"
    device: str = "cpu"
    min_confidence: float = 0.7
    instructions: str = "Which kind of assistant should answer this request?"
    routes: list[Route] = field(default_factory=list)
    default_route: str = ""  # light model, used for easy requests
    heavy_route: str = ""  # strongest local model, used for hard requests
    code_route: str = ""  # optional cloud route for questions about software
    expert_route: str = ""  # optional cloud route for the hardest engineering questions
    image_model: str | None = None

    def route(self, name: str) -> Route | None:
        return next((r for r in self.routes if r.name == name), None)


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
    default_options: dict[str, Any] = field(default_factory=dict)
    router: RouterConfig = field(default_factory=RouterConfig)
    opencode: OpenCodeConfig = field(default_factory=OpenCodeConfig)


def config_dir() -> Path:
    """The folder holding models.yaml and themes.yaml.

    `ollama/config.local/` is not in git and wins over `ollama/config/` when it
    exists, so each machine can keep its own model list without editing the
    repo's default. OLLAMA_PIPELINE_CONFIG overrides both.
    """
    override = os.environ.get("OLLAMA_PIPELINE_CONFIG")
    if override:
        return Path(override)
    default = Path(__file__).resolve().parent.parent / "config"
    local = default.with_name("config.local")
    return local if (local / "models.yaml").exists() else default


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


def _router(raw: dict[str, Any], model_ids: set[str]) -> RouterConfig:
    routes = [
        # `model:` is one id, or a list to try in order.
        Route(name, tuple(r["model"]) if isinstance(r["model"], list) else (r["model"],), r.get("description") or name)
        for name, r in (raw.get("routes") or {}).items()
    ]
    for route in routes:
        for model in route.models:
            if model not in model_ids:
                raise ValueError(f"models.yaml: route '{route.name}' uses unknown model '{model}'")
    names = [r.name for r in routes]
    config = RouterConfig(
        enabled=bool(raw.get("enabled", False)) and bool(routes),
        backend=raw.get("backend") or "von",
        device=raw.get("device") or "cpu",
        min_confidence=float(raw.get("min_confidence", 0.7)),
        instructions=raw.get("instructions") or RouterConfig.instructions,
        routes=routes,
        default_route=raw.get("default_route") or (names[0] if names else ""),
        heavy_route=raw.get("heavy_route") or (names[-1] if names else ""),
        code_route=raw.get("code_route") or "",
        expert_route=raw.get("expert_route") or "",
        image_model=raw.get("image_model"),
    )
    for key in ("default_route", "heavy_route", "code_route", "expert_route"):
        if routes and getattr(config, key) and getattr(config, key) not in names:
            raise ValueError(f"models.yaml: router.{key} '{getattr(config, key)}' is not a defined route")
    if config.image_model and config.image_model not in model_ids:
        raise ValueError(f"models.yaml: router.image_model '{config.image_model}' is not a defined model")
    return config


def load_config(directory: Path | None = None) -> PipelineConfig:
    directory = directory or config_dir()
    log.info("Config: %s", directory)
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
        provider = item.get("provider") or "ollama"
        if provider not in ("ollama", "opencode"):
            raise ValueError(f"models.yaml: model '{item.get('id')}' uses unknown provider '{provider}'")
        host = "opencode" if provider == "opencode" else item.get("host") or default_host
        if provider == "ollama" and host not in hosts:
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
                provider=provider,
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
        default_options=raw.get("default_options") or {},
        router=_router(raw.get("router") or {}, {m.id for m in models}),
        opencode=OpenCodeConfig(**{
            key: value
            for key, value in (raw.get("opencode") or {}).items()
            if key in ("command", "agent", "timeout", "home")
        }),
    )
