"""Which agent skills are switched on. Changed by an admin on the Admin panel.

Kept in `backend/data/admin/skills.json`, not in git. A setting that is not
in the file yet has its default, so a new skill starts as `DEFAULTS` says.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

# What each switch is, for the Admin panel. `id` is the key in skills.json.
SKILLS = [
    {"id": "sql", "name": "Database queries",
     "about": "With a database picked in the chat, the model looks at its tables and runs read-only queries to answer."},
    {"id": "charts", "name": "Charts",
     "about": "The model draws a bar, line, area or pie chart of numbers it has, when asked or when a comparison reads better as a picture."},
    {"id": "memory", "name": "Memory",
     "about": "Facts the team wants kept, such as names, preferences or decisions, given to the model in every chat of their workspace."},
    {"id": "memory_from_chat", "name": "Save and forget from the chat", "parent": "memory",
     "about": "When someone says \"remember that ...\" or \"forget ...\", the model saves or removes a memory itself. Off: only admins change memories."},
]
DEFAULTS = {"sql": True, "charts": True, "memory": True, "memory_from_chat": True}


class SkillSettings:
    def __init__(self, folder: Path):
        self.file = folder / "skills.json"
        self._lock = threading.Lock()

    def get(self) -> dict[str, bool]:
        try:
            saved = json.loads(self.file.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            saved = {}
        return {key: bool(saved.get(key, default)) for key, default in DEFAULTS.items()}

    def update(self, changes: dict[str, Any]) -> dict[str, bool]:
        """Sets the switches named in `changes`; unknown names are ignored."""
        with self._lock:
            current = self.get()
            current.update({k: bool(v) for k, v in changes.items() if k in DEFAULTS})
            self.file.parent.mkdir(parents=True, exist_ok=True)
            temp = self.file.with_suffix(".tmp")
            temp.write_text(json.dumps(current, indent=2), encoding="utf-8")
            os.replace(temp, self.file)
            return current
