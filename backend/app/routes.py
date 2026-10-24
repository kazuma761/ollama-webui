import json
from collections.abc import AsyncIterator
from pathlib import PurePath
from typing import Any

from agent_skills import GENERAL, ChartTool, MemoryTools, Toolbox, wants_chart, wants_memory
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from ollama_pipeline import (
    ModelNotFound,
    OllamaUnavailable,
    PipelineError,
    Registry,
    Router,
    UnsupportedDocument,
    budget_for,
    chat_stream,
    cut_middle,
    estimate,
    picker_models,
    select_local_model,
    select_model,
    select_tool_model,
    todays_theme,
)
from sql_connector import Connection, SheetError, SqlTools, as_markdown, describe, import_file, is_spreadsheet
from starlette.concurrency import run_in_threadpool
from workspace_engine import ChatFile, ChatFiles, WorkspaceError, wants_whole

from .schemas import ChatRequest
from .workspaces.routes import read_file

# A spreadsheet with at most this many cells is small enough to give the model whole, as a table.
# A bigger one is queried with SQL: the model asks for sums and rows, the database does the counting.
SMALL_SHEET_CELLS = 600
TABLE_ABOUT_TOKENS = 1500  # what the model is told about one spreadsheet's tables, at most
NOTE_PROMPT_TOKENS = 600  # the note-taking instructions and the note itself, beside one part of a file

router = APIRouter()


def get_registry(request: Request) -> Registry:
    return request.app.state.registry


def get_router(request: Request) -> Router:
    return request.app.state.router


@router.get("/health")
async def health(registry: Registry = Depends(get_registry)):
    hosts = await registry.health()
    return {"status": "ok" if any(h["ok"] for h in hosts.values()) else "degraded", "hosts": hosts}


@router.get("/models")
async def models(registry: Registry = Depends(get_registry), auto: Router = Depends(get_router)):
    default, entries = await picker_models(registry, auto)
    return {"default": default, "models": entries}


@router.get("/theme")
async def theme(registry: Registry = Depends(get_registry)):
    today = todays_theme(registry.config.themes)
    if today is None:
        raise HTTPException(404, "No themes configured in ollama/config/themes.yaml.")
    return {"title": today.title, "prompt": today.prompt}


@router.post("/files")
async def add_file(request: Request, file: UploadFile):
    """Keeps a file attached in the chat on this machine, and says what it is. Its text is not sent back.

    The chat's messages then carry only the file's id; each question gets the
    part of the file it needs (workspace_engine.ChatFiles). A spreadsheet is
    loaded into SQLite tables that the model queries.
    """
    files: ChatFiles = request.app.state.chatfiles
    name = PurePath(file.filename or "file").name
    megabytes = request.app.state.registry.config.workspaces.max_upload_mb
    data = await file.read(megabytes * 1024 * 1024 + 1)
    if len(data) > megabytes * 1024 * 1024:
        raise HTTPException(413, f"{name} is over {megabytes} MB")
    file_id, folder = files.new()
    try:
        if is_spreadsheet(name):
            tables = await run_in_threadpool(import_file, name, data, folder / "tables.db")
            small = sum(t["rows"] * len(t["columns"]) for t in tables) <= SMALL_SHEET_CELLS
            text = await run_in_threadpool(as_markdown, folder / "tables.db", tables) if small else describe(tables, name)
            kept = await files.add(file_id, folder, name, data, [("", text)], tables=tables, query=not small,
                                   truncated=any(t["truncated"] for t in tables))
        else:
            sections, truncated, pages = await run_in_threadpool(read_file, name, data)
            kept = await files.add(file_id, folder, name, data, sections, truncated=truncated, pages=pages)
    except (UnsupportedDocument, SheetError) as exc:
        files.discard(file_id)
        raise HTTPException(415, str(exc)) from exc
    except ImportError as exc:
        files.discard(file_id)
        raise HTTPException(500, f"Reading {name} needs the {exc.name} package on the server: run `pip install -r requirements.txt`.") from exc
    except BaseException:
        files.discard(file_id)
        raise
    return kept.public()


def _files(store: ChatFiles, messages: list[dict[str, Any]]) -> tuple[list[ChatFile], list[str]]:
    """The conversation's files kept here, the newest turn's first; and the names of those no longer kept."""
    kept, gone, seen = [], [], set()
    for message in reversed(messages):
        for ref in message.get("files") or []:
            if not ref.get("id") or ref["id"] in seen:
                continue
            seen.add(ref["id"])
            file = store.get(ref["id"])
            if file:
                kept.append(file)
            else:
                gone.append(ref.get("name") or "a file")
    return kept, gone


def _without_file_text(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The messages with each kept file reduced to a mention: its text comes with the question, as much as fits."""
    out = []
    for message in messages:
        refs = message.get("files") or []
        names = [f["name"] for f in refs if f.get("id")]
        message = {**message, "files": [f for f in refs if not f.get("id")]}
        if names:
            message["content"] = (message.get("content") or "") + ("\n\n" if message.get("content") else "") \
                + "[Attached: " + ", ".join(names) + "]"
        out.append(message)
    return out


def _about(file: ChatFile) -> str:
    """What the model is told about a spreadsheet's tables, kept short."""
    text = describe(file.tables, file.name)
    if estimate(text) > TABLE_ABOUT_TOKENS:
        text = describe([{**t, "sample": []} for t in file.tables], file.name)
    return cut_middle(text, TABLE_ABOUT_TOKENS)


def _shares(prompt: int, workspace: bool, files: bool) -> tuple[int, int]:
    """Tokens for the workspace's passages and for the files' text; the conversation gets the rest."""
    if workspace and files:
        return int(prompt * 0.25), int(prompt * 0.4)
    if workspace:
        return int(prompt * 0.45), 0
    if files:
        return 0, int(prompt * 0.6)
    return 0, 0


def _assemble(messages: list[dict[str, Any]], entry, stable: list[str], turn: list[str]) -> list[dict[str, Any]]:
    """The messages as sent: what stays the same from turn to turn in the system message (llama-server
    reuses that part of its cache), and what belongs to this question with the question itself."""
    out = [dict(m) for m in messages]
    if turn:
        last = max(i for i, m in enumerate(out) if m["role"] == "user")
        out[last]["content"] = "<context>\n" + "\n\n".join(turn) + "\n</context>\n\n" + (out[last].get("content") or "")
    if stable:
        system = "\n\n".join(part for part in (entry.system, *stable) if part)
        out = [{"role": "system", "content": system}, *out]
    return out


@router.post("/chat")
async def chat(
    body: ChatRequest,
    request: Request,
    registry: Registry = Depends(get_registry),
    auto: Router = Depends(get_router),
):
    """Streams the reply as newline-delimited JSON events: route, sources, notice, thinking,
    delta, tool_call, tool_progress, tool_result, done, error.

    Everything that comes with a question is fitted to what the model can read
    at once (llama-server's slot size): a workspace's passages, the attached
    files (whole when they fit, else the passages that match, or notes on the
    whole file when a summary is asked for), and big spreadsheets as SQL tables
    the model queries. Agent skills an admin switched on are offered when the
    message needs them. Workspaces, files, databases and memories keep the
    reply on a local model.
    """
    state = request.app.state
    messages = [m.model_dump() for m in body.messages]
    skills = state.skills.get()
    asked = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    scope = body.workspace or GENERAL
    current = {f.get("id") for f in (messages[-1].get("files") or []) if f.get("id")}
    attached, gone = _files(state.chatfiles, messages)
    tables = [f for f in attached if f.query]
    texts = [f for f in attached if not f.query]
    try:
        parts, connections, about = [], [], {}
        if body.database:
            if not skills["sql"]:
                raise HTTPException(403, "Answering from databases is turned off on the Admin panel.")
            connection = state.sql_store.get(body.database)
            if connection is None:
                raise HTTPException(404, f"There is no saved database connection named '{body.database}'.")
            connections.append(connection)
        for f in tables:
            db_id, n = f.db_id, 2
            while any(c.id == db_id for c in connections):
                db_id, n = f"{f.db_id} ({n})", n + 1
            connections.append(Connection(id=db_id, engine="sqlite", path=str(f.tables_path)))
            about[db_id] = _about(f)
        sql = registry.config.sql
        if connections:
            parts.append(SqlTools(connections, max_rows=sql.max_rows, timeout=sql.timeout, about=about))
        if skills["charts"] and (body.database or body.workspace or tables or wants_chart(asked)):
            parts.append(ChartTool())
        if skills["memory"] and skills["memory_from_chat"] and wants_memory(asked):
            parts.append(MemoryTools(state.memory, scope))
        tools = Toolbox(parts)

        fallbacks = []
        if tools:
            source = "database" if body.database else "skills"
            entry, route = await select_tool_model(registry, body.model, sql.model, source)
        elif body.workspace or attached:
            entry, route = await select_local_model(registry, auto, body.model, messages)
        else:
            entry, route, fallbacks = await select_model(registry, auto, body.model, messages)

        # What comes with the question, in the room the model has.
        budget = budget_for(entry.budget, body.options)
        ws_share, file_share = _shares(budget.prompt, bool(body.workspace), bool(texts))
        stable, turn, sources, start = [], [], [], 1
        if body.workspace:
            try:
                rules, passages, event = await state.workspaces.context(body.workspace, messages, ws_share, start)
            except WorkspaceError as exc:
                raise HTTPException(404, str(exc)) from exc
            stable.append(rules)
            if passages:
                turn.append(passages)
            if event:
                sources.append(event)
                start += len(event["items"])
        if tools:
            stable.append(tools.system_prompt())
        # Memories go to a model on this machine only (a local model never falls back to the cloud).
        if skills["memory"] and entry.local:
            name = state.workspaces.store.get(body.workspace).meta()["name"] if body.workspace else ""
            remembered = state.memory.prompt(scope, name)
            if remembered:
                stable.append(remembered)

        # A summary of a file too long to read at once: it is read whole, in parts, first (in events()).
        reading: list[ChatFile] = []
        if texts and wants_whole(asked):
            big = [f for f in texts if f.tokens > file_share]
            reading = ([f for f in big if f.id in current] or big[:1])[:2]
        others = [f for f in texts if f not in reading]
        if others:
            block, event = await state.chatfiles.context(others, asked, file_share // (3 if reading else 1), start)
            if block:
                turn.append(block)
            if event:
                sources.append(event)
    except ModelNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except OllamaUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except PipelineError as exc:
        raise HTTPException(400, str(exc)) from exc
    messages = _without_file_text(messages)
    part_tokens = max(1500, min(6000, budget.context - NOTE_PROMPT_TOKENS - 400))

    async def events() -> AsyncIterator[str]:
        try:
            if route:
                yield json.dumps(route) + "\n"
            for event in sources:
                yield json.dumps(event) + "\n"
            if gone:
                yield json.dumps({"type": "notice", "message": (
                    f"{', '.join(gone)}: no longer on the server (attached files are kept for "
                    f"{registry.config.documents.keep_hours} hours). Attach {'it' if len(gone) == 1 else 'them'} again to ask about "
                    f"{'it' if len(gone) == 1 else 'them'}.")}) + "\n"
            for file in reading:
                yield json.dumps({"type": "tool_call", "name": "read_document", "args": {"file": file.name}}) + "\n"
                notes = None
                async for step in state.chatfiles.read_whole(file, entry, part_tokens, file_share // len(reading)):
                    if step["type"] == "progress":
                        yield json.dumps({"type": "tool_progress", "name": "read_document",
                                          "message": f"Reading {file.name}: {step['done']} of {step['total']} parts read"}) + "\n"
                    else:
                        notes = step
                yield json.dumps({"type": "tool_result", "name": "read_document", "file": file.name,
                                  "parts": notes["parts"], "skipped": notes["skipped"]}) + "\n"
                turn.append(notes["text"])
            stream = chat_stream(
                registry, entry, _assemble(messages, entry, stable, turn), body.options, fallbacks,
                tools=tools.definitions if tools else None,
                run_tool=tools.run if tools else None,
                max_rounds=registry.config.sql.max_rounds,
            )
            async for event in stream:
                yield json.dumps(event, default=str) + "\n"
        except PipelineError as exc:
            yield json.dumps({"type": "error", "message": str(exc)}) + "\n"

    return StreamingResponse(
        events(),
        media_type="application/x-ndjson",
        # Keeps nginx and similar proxies from buffering the stream.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
