"""Puts the tools of one chat together, and decides which skills a message needs.

A tool set is anything with `definitions` (Ollama tool definitions),
`system_prompt()` and `async run(name, args) -> (text, summary)`: the SQL
connector's `SqlTools`, `ChartTool`, `MemoryTools`. This module imports none
of them, so each stays in its own package.

Small local models call tools they were offered even when a message does not
need one. So a skill is offered only when the message is about it (the
`wants_*` checks), the way AnythingLLM picks skills per message; an everyday
chat gets no tools at all.
"""

from __future__ import annotations

import re
from typing import Any

_CHART = re.compile(
    r"\b(chart|charts|graph|graphs|plot|plots|visuali[sz](e|ation)|pie|histogram|bar\s*diagram|trend|trends)\b", re.I
)
_MEMORY = re.compile(
    r"\b(remember|memori[sz]e|forget|keep in mind|note that|make a note|don'?t forget|do not forget)\b", re.I
)


def wants_chart(text: str) -> bool:
    return bool(_CHART.search(text or ""))


def wants_memory(text: str) -> bool:
    return bool(_MEMORY.search(text or ""))


class Toolbox:
    def __init__(self, parts: list[Any]):
        self.parts = [p for p in parts if p]
        self._owner = {d["function"]["name"]: p for p in self.parts for d in p.definitions}

    def __bool__(self) -> bool:
        return bool(self.parts)

    @property
    def definitions(self) -> list[dict[str, Any]]:
        return [d for p in self.parts for d in p.definitions]

    def system_prompt(self) -> str:
        return "\n\n".join(p.system_prompt() for p in self.parts if p.system_prompt())

    async def run(self, name: str, args: Any) -> tuple[str, dict[str, Any]]:
        part = self._owner.get(name)
        if part is None:
            known = ", ".join(self._owner) or "none"
            return f"Error: there is no tool named '{name}'. The tools you have: {known}.", {"error": f"no tool {name}"}
        return await part.run(name, args)
