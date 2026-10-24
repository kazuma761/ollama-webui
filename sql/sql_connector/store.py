"""Saved connections, in one JSON file on this machine.

AnythingLLM keeps its connections as JSON in a system setting
(`agent_sql_connections`). Here they live in
`backend/data/sql/connections.json`: not in git, readable by the server's
user only. Passwords are stored as typed (AnythingLLM does the same), so give
the app a database user that can only read.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any

from .connectors import Connection, DatabaseError

_FIELDS = {f.name for f in fields(Connection)}


def slug(name: str) -> str:
    """The id a connection gets from its name: what AnythingLLM's slugify gives."""
    text = re.sub(r"[^\w\s-]", "", name.lower().strip())
    return re.sub(r"[\s_]+", "-", text).strip("-")


class ConnectionStore:
    def __init__(self, folder: Path):
        self.file = folder / "connections.json"
        self._lock = threading.Lock()

    def _read(self) -> list[Connection]:
        try:
            raw = json.loads(self.file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except ValueError as exc:
            raise DatabaseError(f"{self.file} is not valid JSON: {exc}") from exc
        return [Connection(**{k: v for k, v in item.items() if k in _FIELDS}) for item in raw]

    def _write(self, items: list[Connection]) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        temp = self.file.with_suffix(".tmp")
        # Created readable by this user only: the file holds passwords.
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump([asdict(c) for c in items], out, indent=2)
        os.replace(temp, self.file)

    def all(self) -> list[Connection]:
        with self._lock:
            return self._read()

    def get(self, connection_id: str) -> Connection | None:
        return next((c for c in self.all() if c.id == connection_id), None)

    def save(self, connection: Connection, original_id: str | None = None) -> Connection:
        """Adds a connection, or replaces `original_id`. An empty password keeps the one saved before."""
        with self._lock:
            items = self._read()
            old = next((c for c in items if c.id == original_id), None) if original_id else None
            if any(c.id == connection.id and c is not old for c in items):
                raise DatabaseError(f"A connection named '{connection.id}' already exists. Pick another name.")
            if old and not connection.password and old.engine == connection.engine:
                connection.password = old.password
            items = [connection if c is old else c for c in items] if old else [*items, connection]
            self._write(items)
            return connection

    def delete(self, connection_id: str) -> bool:
        with self._lock:
            items = self._read()
            kept = [c for c in items if c.id != connection_id]
            if len(kept) == len(items):
                return False
            self._write(kept)
            return True

    @staticmethod
    def build(data: dict[str, Any]) -> Connection:
        """A Connection from the form on the connections page."""
        name = str(data.get("name") or data.get("id") or "")
        connection_id = slug(name)
        if not connection_id:
            raise DatabaseError("Give the connection a name, e.g. sales-db.")
        port = data.get("port")
        try:
            port = int(port) if port not in (None, "") else None
        except (TypeError, ValueError) as exc:
            raise DatabaseError("The port must be a number.") from exc
        return Connection(
            id=connection_id,
            engine=str(data.get("engine") or ""),
            host=str(data.get("host") or "").strip(),
            port=port,
            user=str(data.get("user") or "").strip(),
            password=str(data.get("password") or ""),
            database=str(data.get("database") or "").strip(),
            schema=str(data.get("schema") or "").strip(),
            ssl=bool(data.get("ssl")),
            path=str(data.get("path") or "").strip(),
        )
