"""Streams answers from OpenCode's cloud models through its own local server, `opencode serve`.

This drives OpenCode exactly the way its `opencode run` command does: create a
session, send the prompt, listen to the event stream, and reject every
permission request. The event stream carries the text as it is written, which
is what lets the chat show the reply live.

OpenCode's free tier only answers requests that come from stock OpenCode, so
nothing about it is customised: the built-in agent, the built-in prompt, the
unmodified program. Three things keep that safe for a web chat:

- the server runs in an empty temporary folder, so there are no project files
  for it to read;
- its shell, edit and web tools are set to "ask", and this client answers
  every such request with "reject", so it cannot run commands, change files
  or fetch from the web on this machine. A request that is never answered
  stays pending - it is never approved by default;
- the server only listens on 127.0.0.1 and needs a random password that
  exists only in this process.

Each reply is one throwaway session: the conversation so far is written into
the prompt, and the session is deleted afterwards.
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import json
import logging
import os
import re
import secrets
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .config import OpenCodeConfig
from .errors import PipelineError

log = logging.getLogger(__name__)

PROVIDER = "opencode"
MODELS_TTL_SECONDS = 600
SERVER_START_SECONDS = 30

# Tools stay listed (removing them makes the free tier refuse the request) but
# every use needs approval, and this client never gives it.
_LOCKED_DOWN = {
    "permission": {
        name: "ask" for name in ("bash", "edit", "webfetch", "websearch", "external_directory", "task")
    }
}

# The session rules `opencode run` itself uses for a non-interactive session.
_SESSION_RULES = [
    {"permission": name, "action": "deny", "pattern": "*"} for name in ("question", "plan_enter", "plan_exit")
]

# Rejecting with a message lets the model carry on and answer in text; a bare
# rejection ends the reply on the spot.
_REJECTION = {
    "reply": "reject",
    "message": "No tools are available in this chat. Do not call any tool; write your complete answer as text.",
}

_PREFACE = (
    "You are answering inside a chat application. Give your complete answer as text, using Markdown "
    "and fenced code blocks for any code. You have no project files and no tools here, so do not try "
    "to read, write or run anything - put everything in the reply."
)

_LISTENING = re.compile(r"listening on (https?://[^\s]+)")

# OpenCode starts helper processes of its own. Putting it in its own process
# group lets all of them be stopped together.
_OWN_GROUP: dict[str, Any] = (
    {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
)


class OpenCodeError(PipelineError):
    """OpenCode could not answer."""


def _kill(pid: int) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, check=False)
        else:
            os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


def build_prompt(messages: list[dict[str, Any]]) -> str:
    """Flattens the chat into one prompt. Attached files and images never leave this machine."""
    lines = [_PREFACE]
    *history, last = messages
    if history:
        lines.append("\nConversation so far:")
        about_private_files = False
        for message in history:
            if message["role"] == "system":
                continue
            text = (message.get("content") or "").strip()
            if message["role"] == "user":
                about_private_files = bool(message.get("files") or message.get("images"))
                if about_private_files:
                    text += "\n[The user attached files here that are not shared with you.]"
            elif about_private_files:
                text = "[A reply about those private files, not shared with you.]"
            lines.append(f"\n{'User' if message['role'] == 'user' else 'Assistant'}: {text}")
        lines.append("\n---\nThe user's new message:")
    lines.append("\n" + (last.get("content") or "").strip())
    return "\n".join(lines)


class OpenCodeClient:
    def __init__(self, config: OpenCodeConfig):
        self.config = config
        self._workdir: str | None = None
        self._server: asyncio.subprocess.Process | None = None
        self._http: httpx.AsyncClient | None = None
        self._start_lock = asyncio.Lock()
        self._models: set[str] = set()
        self._models_at = 0.0
        self._models_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[Any]] = set()
        # Last resort if the backend exits without a clean shutdown.
        atexit.register(self._kill_server)

    # ── Setup ──

    def _executable(self) -> str | None:
        return shutil.which(self.config.command)

    def _env(self) -> dict[str, str]:
        # No self-update: the version that was installed and tested is the one that runs.
        env = {
            **os.environ,
            "OPENCODE_CONFIG_CONTENT": json.dumps(_LOCKED_DOWN),
            "OPENCODE_DISABLE_AUTOUPDATE": "true",
        }
        # Folders of its own. Sharing the default ones with an OpenCode the user runs is not
        # safe: 1.x cannot even open the database that 2.x leaves there.
        for kind in ("data", "state", "config", "cache"):
            env[f"XDG_{kind.upper()}_HOME"] = os.path.join(os.path.expanduser(self.config.home), kind)
        return env

    def _cwd(self) -> str:
        if self._workdir is None or not os.path.isdir(self._workdir):
            self._workdir = tempfile.mkdtemp(prefix="fastshot-opencode-")
        return self._workdir

    def _spawn(self, coroutine: Any) -> None:
        """Runs a coroutine on its own, outside whatever request scheduled it."""
        try:
            task = asyncio.get_running_loop().create_task(coroutine)
        except RuntimeError:  # no loop left: the program is shutting down
            coroutine.close()
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def models(self) -> set[str]:
        """Model ids OpenCode currently offers (`opencode models`), cached for a few minutes."""
        async with self._models_lock:
            if self._models_at and time.monotonic() - self._models_at < MODELS_TTL_SECONDS:
                return self._models
            executable = self._executable()
            found: set[str] = set()
            if executable:
                try:
                    process = await asyncio.create_subprocess_exec(
                        executable, "models",
                        cwd=self._cwd(), env=self._env(),
                        stdin=asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    out, _ = await asyncio.wait_for(process.communicate(), 30)
                    found = {line.strip() for line in out.decode(errors="replace").splitlines() if "/" in line}
                except (OSError, asyncio.TimeoutError):
                    log.warning("OpenCode: could not list models")
            self._models, self._models_at = found, time.monotonic()
            return found

    # ── Server ──

    def _kill_server(self) -> None:
        if self._server is not None and self._server.returncode is None:
            _kill(self._server.pid)
        if self._workdir:
            shutil.rmtree(self._workdir, ignore_errors=True)

    async def _stop_server(self) -> None:
        http, server = self._http, self._server
        self._http = self._server = None
        if http is not None:
            await http.aclose()
        if server is not None and server.returncode is None:
            _kill(server.pid)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(server.wait(), 5)

    async def _server_client(self) -> httpx.AsyncClient:
        """The HTTP client for our OpenCode server, starting the server if it is not running."""
        async with self._start_lock:
            if self._http is not None and self._server is not None and self._server.returncode is None:
                return self._http
            await self._stop_server()

            executable = self._executable()
            if not executable:
                raise OpenCodeError(f"OpenCode is not installed (`{self.config.command}` was not found).")
            password = secrets.token_urlsafe(24)
            try:
                server = await asyncio.create_subprocess_exec(
                    executable, "serve", "--hostname", "127.0.0.1",
                    cwd=self._cwd(),
                    env={**self._env(), "OPENCODE_SERVER_PASSWORD": password},
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    **_OWN_GROUP,
                )
            except OSError as exc:
                raise OpenCodeError(f"OpenCode could not be started: {exc}") from exc
            self._server = server

            # It picks its own free port and prints the address once it is listening.
            url = None
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(SERVER_START_SECONDS):
                    async for line in server.stdout:
                        found = _LISTENING.search(line.decode(errors="replace"))
                        if found:
                            url = found.group(1)
                            break
            if url is None:
                await self._stop_server()
                raise OpenCodeError("OpenCode's server did not start.")

            self._spawn(self._drain(server))
            self._http = httpx.AsyncClient(
                base_url=url, auth=("opencode", password), timeout=httpx.Timeout(10.0, read=None)
            )
            log.info("OpenCode: server ready at %s", url)
            return self._http

    @staticmethod
    async def _drain(server: asyncio.subprocess.Process) -> None:
        """Keeps reading the server's output so a full pipe can never stall it."""
        with contextlib.suppress(Exception):
            async for _ in server.stdout:
                pass

    def warm_up(self) -> None:
        """Starts the server in the background so the first reply does not wait for it."""

        async def start() -> None:
            try:
                await self._server_client()
            except OpenCodeError as exc:
                log.warning("OpenCode: %s", exc)

        if self._executable():
            self._spawn(start())

    async def _discard(self, http: httpx.AsyncClient, session_id: str, abort: bool) -> None:
        with contextlib.suppress(httpx.HTTPError, RuntimeError):
            if abort:
                await http.post(f"/session/{session_id}/abort", timeout=5.0)
            await http.delete(f"/session/{session_id}", timeout=5.0)

    # ── Chat ──

    async def chat_stream(
        self, model: str, messages: list[dict[str, Any]], patient: bool = True
    ) -> AsyncIterator[dict[str, Any]]:
        """Yields thinking and delta events as OpenCode writes them, then a done event.

        When a model's provider fails, OpenCode waits and tries again, several
        times. `patient=False` gives up at the first failure instead, for a
        caller that has another model to turn to.
        """
        http = await self._server_client()
        provider_id, _, model_id = model.partition("/")
        started = time.monotonic()

        session_id: str | None = None
        finished = False
        roles: dict[str, str] = {}  # message id -> "user" | "assistant"
        kinds: dict[str, str] = {}  # part id -> "text" | "reasoning" | "tool" | ...
        sent: dict[str, int] = {}  # part id -> characters already passed on
        usage: dict[str, dict[str, Any]] = {}  # assistant message id -> token counts
        last_text_part: str | None = None
        first_text_at: float | None = None

        try:
            try:
                created = await http.post("/session", json={"title": "Fastshot chat", "permission": _SESSION_RULES})
                created.raise_for_status()
                session_id = created.json()["id"]

                # Subscribe before prompting so no event can be missed.
                async with http.stream("GET", "/event") as events:
                    events.raise_for_status()
                    prompted = await http.post(
                        f"/session/{session_id}/prompt_async",
                        json={
                            "model": {"providerID": provider_id, "modelID": model_id},
                            "agent": self.config.agent,
                            "parts": [{"type": "text", "text": build_prompt(messages)}],
                        },
                    )
                    prompted.raise_for_status()

                    lines = events.aiter_lines()
                    last_progress = time.monotonic()
                    while True:
                        remaining = self.config.timeout - (time.monotonic() - last_progress)
                        try:
                            if remaining <= 0:
                                raise asyncio.TimeoutError
                            line = await asyncio.wait_for(anext(lines), remaining)
                        except asyncio.TimeoutError:
                            raise OpenCodeError(
                                f"OpenCode made no progress for {self.config.timeout} seconds."
                            ) from None
                        except StopAsyncIteration:
                            raise OpenCodeError("OpenCode's server closed the connection.") from None

                        if not line.startswith("data:"):
                            continue
                        try:
                            event = json.loads(line[5:])
                        except ValueError:
                            continue
                        props = event.get("properties") or {}
                        owner = (
                            props.get("sessionID")
                            or (props.get("info") or {}).get("sessionID")
                            or (props.get("part") or {}).get("sessionID")
                        )
                        if owner != session_id:
                            continue  # the stream carries every session on the server
                        last_progress = time.monotonic()
                        kind = event.get("type")

                        piece: tuple[str, str] | None = None  # (part id, new text)
                        if kind == "message.updated":
                            info = props["info"]
                            roles[info["id"]] = info["role"]
                            if info["role"] == "assistant":
                                usage[info["id"]] = info.get("tokens") or {}
                        elif kind == "message.part.updated":
                            part = props["part"]
                            if roles.get(part["messageID"]) != "assistant":
                                continue
                            kinds[part["id"]] = part["type"]
                            status = (part.get("state") or {}).get("status")
                            if part["type"] == "tool" and status in ("completed", "error"):
                                # Only tools that need no permission can complete: reading inside the empty folder.
                                log.info("OpenCode: tool '%s' %s", part.get("tool"), status)
                            if part["type"] in ("text", "reasoning"):
                                # Normally empty: the deltas already delivered this text. This
                                # covers a server that sends whole parts and no deltas.
                                piece = (part["id"], (part.get("text") or "")[sent.get(part["id"], 0):])
                        elif kind == "message.part.delta":
                            if props.get("field") == "text" and props["partID"] in kinds:
                                piece = (props["partID"], props.get("delta") or "")
                        elif kind == "permission.asked":
                            log.info("OpenCode: rejected its request to use '%s'", props.get("permission"))
                            rejected = await http.post(f"/permission/{props['id']}/reply", json=_REJECTION)
                            rejected.raise_for_status()
                        elif kind == "session.status":
                            status = props.get("status") or {}
                            if status.get("type") == "retry" and not patient:
                                raise OpenCodeError(status.get("message") or "its provider is not answering")
                        elif kind == "session.error":
                            error = props.get("error") or {}
                            raise OpenCodeError(
                                (error.get("data") or {}).get("message") or error.get("name") or "OpenCode failed."
                            )
                        elif kind == "session.idle":
                            finished = True
                            break

                        if piece and piece[1]:
                            part_id, text = piece
                            sent[part_id] = sent.get(part_id, 0) + len(text)
                            if kinds[part_id] == "reasoning":
                                yield {"type": "thinking", "content": text}
                            elif kinds[part_id] == "text":
                                # A new block of text after an earlier one starts a new paragraph.
                                gap = "\n\n" if last_text_part not in (None, part_id) else ""
                                last_text_part = part_id
                                first_text_at = first_text_at or time.monotonic()
                                yield {"type": "delta", "content": gap + text}
            except httpx.HTTPStatusError as exc:
                raise OpenCodeError(f"OpenCode's server refused the request (HTTP {exc.response.status_code}).") from exc
            except httpx.HTTPError as exc:
                raise OpenCodeError(f"OpenCode's server stopped responding ({type(exc).__name__}).") from exc

            if first_text_at is None:
                raise OpenCodeError("OpenCode returned no answer.")
            ended = time.monotonic()
            output = sum(tokens.get("output") or 0 for tokens in usage.values()) or None
            writing = ended - first_text_at
            yield {
                "type": "done",
                "stats": {
                    "prompt_tokens": max((tokens.get("input") or 0 for tokens in usage.values()), default=None),
                    "output_tokens": output,
                    "tokens_per_second": round(output / writing, 1) if output and writing > 0.5 else None,
                    "total_seconds": round(ended - started, 2),
                },
            }
        finally:
            # Also reached when the user presses Stop. The cleanup runs as its own task
            # because the request that got us here may already be cancelled.
            if session_id is not None:
                self._spawn(self._discard(http, session_id, abort=not finished))

    async def aclose(self) -> None:
        """Stops the server and removes the temporary folder it ran in."""
        await self._stop_server()
        for task in list(self._tasks):
            task.cancel()
        if self._workdir:
            shutil.rmtree(self._workdir, ignore_errors=True)
            self._workdir = None
