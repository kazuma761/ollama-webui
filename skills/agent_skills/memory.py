"""The memory skill: short facts a team wants kept, given to the model in every chat. No model here.

Like AnythingLLM's memories. Each workspace has its own list, and chats
without a workspace use the "general" one. Admins add, edit and remove
memories on the Admin panel; in a chat the model can save or forget one
when the user asks it to (`MemoryTools`), if the admin allows that.

Kept in `backend/data/admin/memories.json`, not in git. There are no logins
yet, so everyone using a workspace shares its memories.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

GENERAL = "general"
MAX_PER_SCOPE = 200
MAX_CHARS = 500  # one memory
PROMPT_CHARS = 4000  # all of a scope's memories in one system message
_SCOPE = re.compile(r"^[a-z0-9-]{3,60}$")
_WORD = re.compile(r"\w{3,}", re.UNICODE)


class MemoryError_(ValueError):
    """Something the person can act on: an empty memory, a full list, an unknown id."""


_STOP = set("the and that this with from for are was were has have had not you your our their about".split())


def _words(text: str) -> set[str]:
    """The words of a memory, cut to their first four letters, so "closes" meets "closing"."""
    return {w.lower()[:4] for w in _WORD.findall(text) if w.lower() not in _STOP}


class MemoryStore:
    def __init__(self, folder: Path):
        self.file = folder / "memories.json"
        self._lock = threading.Lock()

    def _read(self) -> dict[str, list[dict[str, Any]]]:
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, list[dict[str, Any]]]) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        temp = self.file.with_suffix(".tmp")
        temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, self.file)

    @staticmethod
    def _scope(scope: str | None) -> str:
        scope = scope or GENERAL
        if not _SCOPE.match(scope):
            raise MemoryError_(f"'{scope}' is not a workspace.")
        return scope

    def list(self, scope: str | None = None) -> list[dict[str, Any]]:
        return list(self._read().get(self._scope(scope), []))

    def counts(self) -> dict[str, int]:
        return {scope: len(items) for scope, items in self._read().items()}

    def add(self, scope: str | None, text: str, by: str = "admin") -> dict[str, Any]:
        scope, text = self._scope(scope), " ".join((text or "").split())
        if not text:
            raise MemoryError_("A memory cannot be empty.")
        if len(text) > MAX_CHARS:
            raise MemoryError_(f"A memory can be at most {MAX_CHARS} characters; keep it to one fact.")
        with self._lock:
            data = self._read()
            items = data.setdefault(scope, [])
            same = next((m for m in items if m["text"].lower() == text.lower()), None)
            if same:
                return same
            if len(items) >= MAX_PER_SCOPE:
                raise MemoryError_(f"This list is full ({MAX_PER_SCOPE} memories). Remove some on the Admin panel first.")
            item = {"id": secrets.token_hex(6), "text": text, "added": time.strftime("%Y-%m-%d %H:%M"), "by": by}
            items.append(item)
            self._write(data)
            return item

    def update(self, memory_id: str, text: str) -> dict[str, Any]:
        text = " ".join((text or "").split())
        if not text or len(text) > MAX_CHARS:
            raise MemoryError_(f"A memory must have between 1 and {MAX_CHARS} characters.")
        with self._lock:
            data = self._read()
            for items in data.values():
                for item in items:
                    if item["id"] == memory_id:
                        item["text"] = text
                        item["edited"] = time.strftime("%Y-%m-%d %H:%M")
                        self._write(data)
                        return item
        raise MemoryError_("That memory is no longer there.")

    def remove(self, memory_id: str) -> bool:
        with self._lock:
            data = self._read()
            for scope, items in data.items():
                kept = [m for m in items if m["id"] != memory_id]
                if len(kept) != len(items):
                    data[scope] = kept
                    self._write(data)
                    return True
        return False

    def drop_scope(self, scope: str) -> None:
        """Forgets every memory of a workspace that was deleted."""
        with self._lock:
            data = self._read()
            if data.pop(scope, None) is not None:
                self._write(data)

    def matches(self, scope: str | None, text: str) -> list[tuple[float, dict[str, Any]]]:
        """Memories sharing words with `text`, best first, with the share of its words they have."""
        wanted = _words(text)
        if not wanted:
            return []
        scored = [(len(wanted & _words(m["text"])) / len(wanted), m) for m in self.list(scope)]
        return sorted([(score, m) for score, m in scored if score > 0], key=lambda pair: -pair[0])

    def forget(self, scope: str | None, text: str) -> dict[str, Any] | list[dict[str, Any]] | None:
        """Removes the one memory `text` is about.

        Returns it; or, when several fit about as well, the candidates and
        removes nothing (the model asks which); or None when nothing fits.
        """
        found = self.matches(scope, text)
        if not found:
            return None
        best_score, best = found[0]
        rivals = [m for score, m in found[1:] if score >= best_score - 0.15]
        if rivals and best_score < 0.75:
            return [best, *rivals][:5]
        if best_score < 0.5 and len(found) > 1:
            return [m for _, m in found[:5]]
        self.remove(best["id"])
        return best

    def prompt(self, scope: str | None, name: str = "") -> str:
        """The scope's memories for the system message; "" when there are none."""
        items = self.list(scope)
        if not items:
            return ""
        lines, used = [], 0
        for item in reversed(items):  # the newest first, so they are the ones kept when the list is long
            line = f"- {item['text']}"
            if used + len(line) > PROMPT_CHARS:
                break
            lines.append(line)
            used += len(line) + 1
        where = f' for the workspace "{name}"' if name else ""
        return (
            f"Things the team asked you to remember{where}. Use them when they are relevant; "
            "do not list them unless asked.\n" + "\n".join(reversed(lines))
        )


_TEXT = {"type": "string", "description": "One short sentence, e.g. The finance head is Priya Rao."}

DEFINITIONS = [
    {"type": "function", "function": {
        "name": "save_memory",
        "description": "Save one fact to remember in later chats. Only when the user asks you to remember something.",
        "parameters": {"type": "object", "properties": {"text": _TEXT}, "required": ["text"]},
    }},
    {"type": "function", "function": {
        "name": "forget_memory",
        "description": "Remove a saved fact. Only when the user asks you to forget something.",
        "parameters": {"type": "object", "properties": {"text": {"type": "string", "description": "The fact to forget, in a few words"}},
                       "required": ["text"]},
    }},
]

PROMPT = (
    "When the user asks you to remember something, call save_memory with it as one short sentence. "
    "When they ask you to forget something, call forget_memory. Do not save anything they did not ask you to."
)


class MemoryTools:
    """save_memory and forget_memory, for one scope."""

    definitions = DEFINITIONS

    def __init__(self, store: MemoryStore, scope: str | None):
        self.store, self.scope = store, scope or GENERAL

    def system_prompt(self) -> str:
        return PROMPT

    async def run(self, name: str, args: Any) -> tuple[str, dict[str, Any]]:
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {"text": args}
        text = str((args or {}).get("text") or (args or {}).get("memory") or "") if isinstance(args, dict) else ""
        try:
            if name == "save_memory":
                item = self.store.add(self.scope, text, by="chat")
                return f"Saved: {item['text']}", {"memory": item["text"], "saved": True}
            if name == "forget_memory":
                gone = self.store.forget(self.scope, text)
                if gone is None:
                    return "No saved memory matches that; nothing was removed.", {"memory": text, "forgotten": False}
                if isinstance(gone, list):
                    listed = "; ".join(m["text"] for m in gone)
                    return (f"Several memories could be meant, so nothing was removed: {listed}. "
                            "Ask the user which one to forget, then call forget_memory with its words."), \
                        {"memory": text, "forgotten": False}
                return f"Forgotten: {gone['text']}", {"memory": gone["text"], "forgotten": True}
        except MemoryError_ as exc:
            return f"Error: {exc}", {"error": str(exc)}
        return f"Error: there is no tool named '{name}'.", {"error": f"unknown tool {name}"}
