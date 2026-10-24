"""Loads hosts, model aliases and themes from ollama/config/*.yaml."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
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
    vision: bool | None = None  # None = ask the server
    tools: bool | None = None  # None = ask the server whether the model can call tools
    # "ollama" = local: answered on one of `hosts:`, an Ollama server or a llama-server alike.
    # "opencode" = cloud.
    provider: str = "ollama"


@dataclass(frozen=True)
class OpenCodeConfig:
    command: str = "opencode"
    agent: str = "plan"  # must be one of OpenCode's built-in agents for the free tier to answer
    timeout: int = 120  # seconds without any progress before giving up
    # Where the app's own OpenCode keeps its data, apart from any OpenCode the user runs.
    home: str = str(Path(__file__).resolve().parent.parent / ".opencode")


@dataclass(frozen=True)
class DocumentsConfig:
    """Settings of the Word-documents page."""

    model: str = ""  # id of the local model that reads and fills documents; empty = the heavy route's model
    max_jobs: int = 2  # document jobs asking the model at the same time; the rest wait their turn
    second_check: bool = False  # ask the model a second time whether values it did not copy are supported
    keep_hours: int = 24  # uploaded and generated files are deleted after this long
    allow_cloud: bool = False  # true lets OpenCode's cloud models work on documents - for testing only


@dataclass(frozen=True)
class InvoicesConfig:
    """Settings of the Invoices tab of the documents page."""

    model: str = ""  # id of the local model that reads invoices; empty = the first local model that reads pictures
    max_jobs: int = 1  # files read at the same time; the rest wait their turn
    max_pages: int = 40  # pages read from one file
    image_side: int = 1600  # size of the page picture sent to the model: the longer side of an A4 page, in pixels
    date_format: str = "%d-%b-%Y"  # how dates are written in the sheet: 31-Mar-2026


@dataclass(frozen=True)
class SqlConfig:
    """Settings of the SQL connector: the chat answering from a database."""

    model: str = ""  # id of the local model that queries databases; empty = the first local model that calls tools
    max_rows: int = 200  # rows one query may return to the model
    timeout: int = 30  # seconds one query may run before the database stops it
    max_rounds: int = 8  # tool calls in a row before the model must answer with what it has


@dataclass(frozen=True)
class WorkspacesConfig:
    """Settings of workspaces: documents a team adds once, searched for every question."""

    embed_host: str = ""  # host (under `hosts:`) that serves the embedding model; empty = default_host
    embed_model: str = ""  # embedding model on that host; empty = keyword search only
    query_prefix: str = ""  # put before a question when it is embedded (nomic-embed-text wants "search_query: ")
    document_prefix: str = ""  # put before a passage when it is embedded ("search_document: ")
    chunk_chars: int = 1200  # length of one passage
    overlap_chars: int = 200  # how much a passage repeats of the one before, so nothing falls between two
    top_k: int = 6  # passages given to the model with each question
    max_upload_mb: int = 50  # largest file that can be added


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
    documents: DocumentsConfig = field(default_factory=DocumentsConfig)
    invoices: InvoicesConfig = field(default_factory=InvoicesConfig)
    sql: SqlConfig = field(default_factory=SqlConfig)
    workspaces: WorkspacesConfig = field(default_factory=WorkspacesConfig)
    # What runs on each host: "ollama", or "llamacpp" for a llama.cpp llama-server.
    host_kinds: dict[str, str] = field(default_factory=dict)


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


HOST_KINDS = ("ollama", "llamacpp")
_DEFAULT_PORTS = {"ollama": 11434, "llamacpp": 8080}


def _normalize_url(url: str, kind: str = "ollama") -> str:
    """Accepts the forms OLLAMA_HOST allows: host, host:port or a full URL."""
    url = url.strip().rstrip("/")
    if "://" not in url:
        url = f"http://{url}"
    if not re.search(r":\d+$", url):
        url = f"{url}:{_DEFAULT_PORTS[kind]}"
    return url


def _host(name: str, value: Any) -> tuple[str, str]:
    """A `hosts:` entry: a URL (an Ollama server), or {url: ..., kind: ollama | llamacpp}."""
    if isinstance(value, dict):
        kind = value.get("kind") or "ollama"
        if kind not in HOST_KINDS:
            raise ValueError(f"models.yaml: host '{name}' has unknown kind '{kind}' (use ollama or llamacpp)")
        if not value.get("url"):
            raise ValueError(f"models.yaml: host '{name}' needs a url")
        return _normalize_url(str(value["url"]), kind), kind
    return _normalize_url(str(value)), "ollama"


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


def _documents(raw: dict[str, Any], models: dict[str, ModelAlias]) -> DocumentsConfig:
    model = raw.get("model") or ""
    if model and model not in models:
        raise ValueError(f"models.yaml: documents.model '{model}' is not a defined model")
    if model and models[model].provider != "ollama" and not raw.get("allow_cloud"):
        raise ValueError(f"models.yaml: documents.model '{model}' must be a local (Ollama) model")
    return DocumentsConfig(
        model=model,
        max_jobs=max(1, int(raw.get("max_jobs", 2))),
        second_check=bool(raw.get("second_check", False)),
        keep_hours=max(1, int(raw.get("keep_hours", 24))),
        allow_cloud=bool(raw.get("allow_cloud", False)),
    )


def _invoices(raw: dict[str, Any], models: dict[str, ModelAlias]) -> InvoicesConfig:
    model = raw.get("model") or ""
    if model and model not in models:
        raise ValueError(f"models.yaml: invoices.model '{model}' is not a defined model")
    if model and models[model].provider != "ollama":
        raise ValueError(f"models.yaml: invoices.model '{model}' must be a local (Ollama) model")
    date_format = str(raw.get("date_format") or InvoicesConfig.date_format)
    try:
        datetime.strptime(datetime(2026, 3, 31).strftime(date_format), date_format)
    except ValueError as exc:
        raise ValueError(f"models.yaml: invoices.date_format '{date_format}' is not a usable date format") from exc
    return InvoicesConfig(
        model=model,
        max_jobs=max(1, int(raw.get("max_jobs", 1))),
        max_pages=max(1, int(raw.get("max_pages", 40))),
        image_side=min(3000, max(800, int(raw.get("image_side", 1600)))),
        date_format=date_format,
    )


def _sql(raw: dict[str, Any], models: dict[str, ModelAlias]) -> SqlConfig:
    model = raw.get("model") or ""
    if model and model not in models:
        raise ValueError(f"models.yaml: sql.model '{model}' is not a defined model")
    if model and models[model].provider != "ollama":
        raise ValueError(f"models.yaml: sql.model '{model}' must be a local (Ollama) model")
    return SqlConfig(
        model=model,
        max_rows=min(5000, max(1, int(raw.get("max_rows", 200)))),
        timeout=min(600, max(1, int(raw.get("timeout", 30)))),
        max_rounds=min(20, max(1, int(raw.get("max_rounds", 8)))),
    )


def _workspaces(raw: dict[str, Any], hosts: dict[str, str], default_host: str) -> WorkspacesConfig:
    host = raw.get("embed_host") or default_host
    if host not in hosts:
        raise ValueError(f"models.yaml: workspaces.embed_host '{host}' is not one of hosts:")
    chunk = min(4000, max(300, int(raw.get("chunk_chars", 1200))))
    return WorkspacesConfig(
        embed_host=host,
        embed_model=str(raw.get("embed_model") or ""),
        query_prefix=str(raw.get("query_prefix") or ""),
        document_prefix=str(raw.get("document_prefix") or ""),
        chunk_chars=chunk,
        overlap_chars=min(chunk // 2, max(0, int(raw.get("overlap_chars", 200)))),
        top_k=min(20, max(1, int(raw.get("top_k", 6)))),
        max_upload_mb=min(500, max(1, int(raw.get("max_upload_mb", 50)))),
    )


def load_config(directory: Path | None = None) -> PipelineConfig:
    directory = directory or config_dir()
    log.info("Config: %s", directory)
    raw = _read(directory / "models.yaml")

    parsed = {name: _host(name, value) for name, value in (raw.get("hosts") or {}).items()}
    hosts = {name: url for name, (url, _) in parsed.items()}
    kinds = {name: kind for name, (_, kind) in parsed.items()}
    default_host = raw.get("default_host") or next(iter(hosts), "local")
    hosts.setdefault(default_host, DEFAULT_URL)
    kinds.setdefault(default_host, "ollama")

    for name in hosts:
        override = os.environ.get(f"OLLAMA_HOST_{re.sub(r'[^A-Za-z0-9]', '_', name).upper()}")
        if override:
            hosts[name] = _normalize_url(override, kinds[name])
    if os.environ.get("OLLAMA_HOST"):
        hosts[default_host] = _normalize_url(os.environ["OLLAMA_HOST"], kinds[default_host])

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
                tools=item.get("tools"),
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
        documents=_documents(raw.get("documents") or {}, {m.id: m for m in models}),
        invoices=_invoices(raw.get("invoices") or {}, {m.id: m for m in models}),
        sql=_sql(raw.get("sql") or {}, {m.id: m for m in models}),
        host_kinds=kinds,
        workspaces=_workspaces(raw.get("workspaces") or {}, hosts, default_host),
    )
