"""Runs OpenCode's cloud models through its own headless command, `opencode run`.

OpenCode's free tier only answers requests that come from stock OpenCode, so
this deliberately changes nothing about it: the built-in agent, the built-in
prompt, the unmodified CLI. Two things keep that safe for a web chat:

- every run happens in an empty temporary folder, so there are no project
  files for it to read;
- its permissions are set to "ask", and headless mode rejects every request it
  would have to ask about, so it cannot run shell commands, edit files or
  fetch from the web on this machine.

Each call is one stateless run: the conversation so far is written into the
prompt, nothing is kept between calls on our side.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import AsyncIterator
from typing import Any

from .config import OpenCodeConfig
from .errors import PipelineError

log = logging.getLogger(__name__)

PROVIDER = "opencode"
MODELS_TTL_SECONDS = 600

# Tools stay listed (removing them makes the free tier refuse the request) but
# every use needs approval, which a headless run cannot give.
_LOCKED_DOWN = {
    "permission": {
        name: "ask" for name in ("bash", "edit", "webfetch", "websearch", "external_directory", "task")
    }
}

_PREFACE = (
    "You are answering inside a chat application. Give your complete answer as text, using Markdown "
    "and fenced code blocks for any code. You have no project files and no tools here, so do not try "
    "to read, write or run anything - put everything in the reply."
)


class OpenCodeError(PipelineError):
    """OpenCode could not answer."""


# OpenCode starts helper processes of its own. They inherit its output pipe, so
# killing only the parent would leave us waiting on that pipe until they exit.
_OWN_GROUP: dict[str, Any] = (
    {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
)


async def _kill_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True, check=False)
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        await asyncio.wait_for(process.wait(), 5)
    except asyncio.TimeoutError:
        log.warning("OpenCode: process %s did not exit after being killed", process.pid)


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
        self._models: set[str] = set()
        self._models_at = 0.0
        self._lock = asyncio.Lock()

    def _executable(self) -> str | None:
        return shutil.which(self.config.command)

    def _env(self) -> dict[str, str]:
        return {**os.environ, "OPENCODE_CONFIG_CONTENT": json.dumps(_LOCKED_DOWN)}

    def _cwd(self) -> str:
        if self._workdir is None or not os.path.isdir(self._workdir):
            self._workdir = tempfile.mkdtemp(prefix="fastshot-opencode-")
        return self._workdir

    def close(self) -> None:
        """Removes the temporary folder OpenCode ran in."""
        if self._workdir:
            shutil.rmtree(self._workdir, ignore_errors=True)
            self._workdir = None

    async def models(self) -> set[str]:
        """Model ids OpenCode currently offers (`opencode models`), cached for a few minutes."""
        async with self._lock:
            if time.monotonic() - self._models_at < MODELS_TTL_SECONDS and self._models_at:
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

    async def chat_stream(self, model: str, messages: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
        """Yields delta events as OpenCode finishes each block of text, then a done event."""
        executable = self._executable()
        if not executable:
            raise OpenCodeError(f"OpenCode is not installed (`{self.config.command}` was not found).")

        started = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                executable, "run", "--model", model, "--agent", self.config.agent, "--format", "json",
                cwd=self._cwd(), env=self._env(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                limit=16 * 1024 * 1024,  # one JSON event per line; a long answer is one long line
                **_OWN_GROUP,
            )
        except OSError as exc:
            raise OpenCodeError(f"OpenCode could not be started: {exc}") from exc

        answered = False
        tokens: dict[str, Any] = {}
        try:
            process.stdin.write(build_prompt(messages).encode())
            await process.stdin.drain()
            process.stdin.close()

            deadline = started + self.config.timeout
            while True:
                try:
                    line = await asyncio.wait_for(process.stdout.readline(), max(deadline - time.monotonic(), 0.1))
                except asyncio.TimeoutError:
                    raise OpenCodeError(f"OpenCode did not finish within {self.config.timeout} seconds.") from None
                if not line:
                    break
                try:
                    event = json.loads(line)
                except ValueError:
                    continue  # plain log lines, e.g. "permission requested ... auto-rejecting"
                kind, part = event.get("type"), event.get("part") or {}
                if kind == "text" and part.get("text"):
                    # Text arrives a finished block at a time; blocks are separated like paragraphs.
                    yield {"type": "delta", "content": ("\n\n" if answered else "") + part["text"]}
                    answered = True
                elif kind == "step_finish":
                    tokens = part.get("tokens") or tokens
                elif kind == "error":
                    data = (event.get("error") or {}).get("data") or {}
                    raise OpenCodeError(data.get("message") or (event.get("error") or {}).get("name") or "OpenCode failed.")

            code = await process.wait()
            if not answered:
                raise OpenCodeError(f"OpenCode returned no answer (exit code {code}).")
            yield {
                "type": "done",
                "stats": {
                    "prompt_tokens": tokens.get("input"),
                    "output_tokens": tokens.get("output"),
                    "tokens_per_second": None,
                    "total_seconds": round(time.monotonic() - started, 2),
                },
            }
        finally:
            # Also reached when the user presses Stop or the request is cancelled.
            await _kill_tree(process)
