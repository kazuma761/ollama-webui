"""The four tools the model can call, as AnythingLLM's SQL agent has them.

  list_databases                      which connections this chat may use
  list_tables(database_id)            the tables and views of one
  get_table_schema(database_id, table_name)   the columns of one table
  query(database_id, sql_query)       one read-only query, at most `max_rows` rows

`SqlTools` gives the definitions in the form Ollama's /api/chat takes
(`tools=[...]`), and runs a call the model made. Only the connections it was
made with can be reached: the chat picks them, the model cannot ask for others.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .connectors import Connection, DatabaseError, connector
from .safety import UnsafeQuery, check

log = logging.getLogger(__name__)

PREVIEW_ROWS = 20  # rows of a result shown on the page under the query


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


_DATABASE = {"type": "string", "description": "The database_id, as list_databases gives it."}

DEFINITIONS = [
    _tool(
        "list_databases",
        "List the databases you can use. Returns each one's database_id, its kind (SQLite, PostgreSQL, MySQL) and name.",
        {},
        [],
    ),
    _tool(
        "list_tables",
        "List the tables and views in a database.",
        {"database_id": _DATABASE},
        ["database_id"],
    ),
    _tool(
        "get_table_schema",
        "Get the columns of one table or view: their names, types, and whether they can be empty.",
        {"database_id": _DATABASE, "table_name": {"type": "string", "description": "The table's name, as list_tables gives it."}},
        ["database_id", "table_name"],
    ),
    _tool(
        "query",
        "Run one read-only SQL query (SELECT, or WITH ... SELECT) and get the rows back. Statements that change "
        "data are refused. Use the dialect of the database's kind, name only columns the schema has, and add a "
        "LIMIT when many rows could match.",
        {"database_id": _DATABASE, "sql_query": {"type": "string", "description": "The SQL query to run."}},
        ["database_id", "sql_query"],
    ),
]

PROMPT = """\
You can read the user's data with tools, by its database_id:
{databases}
Answer questions about the data by querying it; never guess numbers, and never add up rows yourself: let SQL
count, sum, average and group (COUNT, SUM, AVG, GROUP BY, ORDER BY ... LIMIT). Work in this order: the tables
listed above or list_tables, get_table_schema if you are unsure of a column, then query. Write one read-only
SELECT at a time in the database's own SQL dialect (SQLite for attached spreadsheets); you cannot change data.
If a query fails, read the error, fix the query and try again. When you have the rows, answer in plain words,
show figures exactly as returned, and put several rows in a Markdown table. Say which query you ran when it
helps the user check the answer."""


class SqlTools:
    def __init__(self, connections: list[Connection], *, max_rows: int = 200, timeout: int = 30, max_chars: int = 12_000,
                 about: dict[str, str] | None = None):
        """`about` describes a connection to the model in place of its `describe()`: for an
        attached spreadsheet, its tables, columns and first rows."""
        self.connections = {c.id: c for c in connections}
        self.max_rows = max_rows
        self.timeout = timeout
        self.max_chars = max_chars
        self.about = about or {}

    definitions = DEFINITIONS

    def system_prompt(self) -> str:
        lines = "\n".join(f"- database_id `{c.id}`: {self.about.get(c.id) or c.describe()}" for c in self.connections.values())
        return PROMPT.format(databases=lines)

    def _connection(self, args: dict[str, Any]) -> Connection:
        wanted = str(args.get("database_id") or args.get("database") or "").strip()
        if not wanted and len(self.connections) == 1:
            return next(iter(self.connections.values()))
        if wanted in self.connections:
            return self.connections[wanted]
        known = ", ".join(self.connections) or "none"
        raise DatabaseError(f"No database '{wanted}' is open in this chat. The ones you can use: {known}.")

    def _text(self, payload: dict[str, Any]) -> str:
        text = json.dumps(payload, ensure_ascii=False, default=str)
        if len(text) <= self.max_chars:
            return text
        return text[: self.max_chars] + " ... [cut: the result is too long to read whole. Select fewer columns or rows.]"

    async def run(self, name: str, args: Any) -> tuple[str, dict[str, Any]]:
        """Runs one call. Returns what the model reads, and a summary for the page.

        Never raises: a refused or failed call is answered with the reason, so
        the model can correct itself, as AnythingLLM's tools do.
        """
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except ValueError:
                args = {}
        args = args if isinstance(args, dict) else {}
        event: dict[str, Any] = {"name": name}
        try:
            if name == "list_databases":
                listed = [{"database_id": c.id, "engine": c.engine, "about": c.describe()} for c in self.connections.values()]
                event.update(rows=len(listed))
                return self._text({"databases": listed}), event

            connection = self._connection(args)
            event["database"] = connection.id
            db = connector(connection, self.timeout)
            if name == "list_tables":
                result = await asyncio.to_thread(db.list_tables)
            elif name == "get_table_schema":
                table = str(args.get("table_name") or args.get("table") or "").strip()
                event["table"] = table
                result = await asyncio.to_thread(db.table_schema, table)
            elif name == "query":
                sql = str(args.get("sql_query") or args.get("sql") or args.get("query") or "")
                event["sql"] = sql
                result = await asyncio.to_thread(db.run, check(sql, connection.engine), None, self.max_rows)
            else:
                raise DatabaseError(f"There is no tool named '{name}'. Use list_tables, get_table_schema or query.")
        except (DatabaseError, UnsafeQuery) as exc:
            log.info("SQL: %s refused or failed: %s", name, exc)
            event["error"] = str(exc)
            return f"Error: {exc}", event

        log.info("SQL: %s on %s -> %d rows", name, event.get("database"), len(result.rows))
        event.update(
            columns=result.columns,
            rows=len(result.rows),
            truncated=result.truncated,
            preview=result.rows[:PREVIEW_ROWS],
        )
        payload: dict[str, Any] = {"columns": result.columns, "rows": result.rows, "row_count": len(result.rows)}
        if result.truncated:
            payload["note"] = f"Only the first {self.max_rows} rows were read. Narrow the query or use COUNT/SUM."
        return self._text(payload), event
