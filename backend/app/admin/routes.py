"""/api/admin/... : the Admin panel (admin.html) - which agent skills are on, and the memories.

Every route needs the password in `ADMIN_PASSWORD` (backend/.env), sent as the
`X-Admin-Password` header. Without one set, the panel is closed. This stands in
for real users and roles until the app has logins.

`/api/skills` (the `public` router) is read-only and open: the chat page asks
it which skills are on, to hide the database picker when queries are off.
"""

import asyncio
import hmac

from agent_skills import GENERAL, SKILLS, MemoryError_
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from ..settings import settings

WRONG_PASSWORD_DELAY = 1.0  # seconds: makes guessing the password slow


async def admin(x_admin_password: str | None = Header(default=None)) -> None:
    expected = settings.admin_password
    if not expected:
        raise HTTPException(403, "The Admin panel is closed. Set ADMIN_PASSWORD in backend/.env and restart the app.")
    if not x_admin_password or not hmac.compare_digest(x_admin_password.encode(), expected.encode()):
        await asyncio.sleep(WRONG_PASSWORD_DELAY)
        raise HTTPException(401, "That is not the admin password.")


router = APIRouter(dependencies=[Depends(admin)])
public = APIRouter()


class SkillChanges(BaseModel):
    sql: bool | None = None
    charts: bool | None = None
    memory: bool | None = None
    memory_from_chat: bool | None = None


class NewMemory(BaseModel):
    scope: str = GENERAL
    text: str = Field(max_length=2000)


class MemoryText(BaseModel):
    text: str = Field(max_length=2000)


@public.get("")
def skills_on(request: Request):
    """Which skills are on. Read by the chat page."""
    return request.app.state.skills.get()


@router.post("/login")
def login():
    """Checks the password (the dependency does the work)."""
    return {"ok": True}


async def _tools_model(request: Request) -> dict:
    """The local model that runs the skills (`sql.model`), and whether it can call tools."""
    registry = request.app.state.registry
    wanted = registry.config.sql.model
    try:
        entries = await registry.list_models()
    except Exception:  # the model servers are down: the panel still works
        entries = []
    entry = next((e for e in entries if e.id == wanted), None)
    if entry is None:
        entry = next((e for e in entries if e.available and e.local and e.tools), None)
    if entry is None:
        return {"label": wanted or "", "ready": False, "note": "No local model that can call tools is running."}
    ready = entry.available and entry.tools is not False
    note = "" if ready else ("Not running." if not entry.available else "This model cannot call tools.")
    return {"label": entry.label, "ready": ready, "tools": entry.tools, "note": note}


@router.get("/skills")
async def skills(request: Request):
    return {"skills": SKILLS, "on": request.app.state.skills.get(), "model": await _tools_model(request)}


@router.post("/skills")
def set_skills(changes: SkillChanges, request: Request):
    return {"on": request.app.state.skills.update(changes.model_dump(exclude_none=True))}


def _scopes(request: Request) -> list[dict]:
    counts = request.app.state.memory.counts()
    scopes = [{"id": GENERAL, "name": "General (chats without a workspace)", "count": counts.get(GENERAL, 0)}]
    for w in request.app.state.workspaces.list():
        scopes.append({"id": w["id"], "name": w["name"], "count": counts.get(w["id"], 0)})
    return scopes


def _known(request: Request, scope: str) -> str:
    if scope != GENERAL and scope not in {s["id"] for s in _scopes(request)}:
        raise HTTPException(404, "There is no such workspace any more.")
    return scope


@router.get("/memories")
def memories(request: Request, scope: str = GENERAL):
    scope = _known(request, scope)
    return {"scope": scope, "memories": request.app.state.memory.list(scope), "scopes": _scopes(request)}


@router.post("/memories")
def add_memory(body: NewMemory, request: Request):
    try:
        return request.app.state.memory.add(_known(request, body.scope), body.text, by="admin")
    except MemoryError_ as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/memories/{memory_id}")
def edit_memory(memory_id: str, body: MemoryText, request: Request):
    try:
        return request.app.state.memory.update(memory_id, body.text)
    except MemoryError_ as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/memories/{memory_id}")
def remove_memory(memory_id: str, request: Request):
    if not request.app.state.memory.remove(memory_id):
        raise HTTPException(404, "That memory is no longer there.")
    return {"ok": True}
