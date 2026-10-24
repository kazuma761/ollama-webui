"""One class per kind of database: open it read-only, list its tables, describe one, run a query.

The same shape as AnythingLLM's `SQLConnectors/` (`runQuery`, `getTablesSql`,
`getTableSchemaSql`, `validateConnection`), in Python. As there, every call
opens its own connection and closes it again: a model asks a few questions a
minute, and nothing is left open between them.

Every method is blocking; `tools.py` runs them in a thread.
"""

from __future__ import annotations

import base64
import datetime
import decimal
import importlib.util
import re
import sqlite3
import time
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ENGINES = {"sqlite": "SQLite", "postgresql": "PostgreSQL", "mysql": "MySQL / MariaDB"}
_DRIVERS = {"postgresql": "psycopg", "mysql": "pymysql"}
CONNECT_TIMEOUT = 10  # seconds to reach a database server
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


class DatabaseError(Exception):
    """A connection or query failed; the message is meant for the person and the model."""


@dataclass
class Connection:
    """A saved connection. `id` is what the model calls it (AnythingLLM's `database_id`)."""

    id: str
    engine: str  # one of ENGINES
    host: str = ""
    port: int | None = None
    user: str = ""
    password: str = ""
    database: str = ""
    schema: str = ""  # PostgreSQL only: the schema whose tables are listed; empty = public
    ssl: bool = False  # encrypt, without checking the server's certificate (as AnythingLLM's "SSL")
    path: str = ""  # SQLite only: the database file on this machine

    def public(self) -> dict[str, Any]:
        """For the browser: everything but the password."""
        data = asdict(self)
        data["password"] = bool(self.password)
        data["label"] = ENGINES.get(self.engine, self.engine)
        return data

    def describe(self) -> str:
        """One line for the model and the log, without the password."""
        if self.engine == "sqlite":
            return f"SQLite file {Path(self.path).name}"
        where = f"{self.host}:{self.port}" if self.port else self.host
        return f"{ENGINES[self.engine]} database {self.database} on {where}"


def missing_driver(engine: str) -> str | None:
    """The Python package this kind of database still needs, or None when it can be used."""
    module = _DRIVERS.get(engine)
    return module if module and importlib.util.find_spec(module) is None else None


@dataclass
class Result:
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    truncated: bool = False  # there were more rows than were read


def _value(value: Any) -> Any:
    """A database value as something JSON can carry."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, decimal.Decimal):
        return int(value) if value == value.to_integral_value() and abs(value) < 2**53 else float(value)
    if isinstance(value, (datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, datetime.timedelta):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        data = bytes(value)
        return f"<{len(data)} bytes>" if len(data) > 64 else "base64:" + base64.b64encode(data).decode()
    if isinstance(value, (list, tuple)):
        return [_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _value(v) for k, v in value.items()}
    return str(value)


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip() or type(exc).__name__
    return text.splitlines()[0][:500]


class Connector:
    """The common part: a DB-API connection, read-only, one query at a time."""

    engine = ""

    def __init__(self, connection: Connection, timeout: int = 30):
        self.connection = connection
        self.timeout = timeout

    # Each kind of database fills these in.
    def _open(self) -> Any:
        raise NotImplementedError

    def _begin(self, con: Any) -> None:
        """Makes the work that follows read-only and time-limited."""

    def _tables_sql(self) -> tuple[str, Sequence[Any]]:
        raise NotImplementedError

    def _schema_sql(self, table: str) -> tuple[str, Sequence[Any]]:
        raise NotImplementedError

    def _timed_out(self, exc: BaseException) -> bool:
        return False

    def _execute(self, cur: Any, sql: str, params: Sequence[Any] | None) -> None:
        """Runs exactly one statement. SQLite and PyMySQL refuse a second one by themselves."""
        # Parameters only when there are any: without them a literal % in the query is left alone.
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)

    def run(self, sql: str, params: Sequence[Any] | None = None, max_rows: int = 200) -> Result:
        """Runs one statement and reads at most `max_rows` rows. Never commits."""
        try:
            con = self._open()
        except DatabaseError:
            raise
        except Exception as exc:  # each driver has its own error classes
            raise DatabaseError(f"Could not connect to {self.connection.describe()}: {_first_line(exc)}") from exc
        try:
            self._begin(con)
            cur = con.cursor()
            self._execute(cur, sql, params)
            result = Result(columns=[d[0] for d in cur.description or []])
            if cur.description:
                rows = cur.fetchmany(max_rows + 1)
                result.truncated = len(rows) > max_rows
                result.rows = [[_value(v) for v in row] for row in rows[:max_rows]]
            return result
        except DatabaseError:
            raise
        except Exception as exc:
            if self._timed_out(exc):
                raise DatabaseError(f"The query took longer than {self.timeout} s and was stopped.") from exc
            raise DatabaseError(_first_line(exc)) from exc
        finally:
            try:
                con.rollback()
            except Exception:
                pass
            con.close()

    def validate(self) -> None:
        """Connects and runs SELECT 1, as AnythingLLM does before a connection is saved."""
        self.run("SELECT 1")

    def list_tables(self) -> Result:
        sql, params = self._tables_sql()
        return self.run(sql, params, max_rows=2000)

    def table_schema(self, table: str) -> Result:
        sql, params = self._schema_sql(table)
        result = self.run(sql, params, max_rows=2000)
        if not result.rows:
            raise DatabaseError(f"There is no table or view named '{table}'. List the tables to see their names.")
        return result


class SQLite(Connector):
    engine = "sqlite"

    def _open(self) -> sqlite3.Connection:
        path = Path(self.connection.path).expanduser()
        if not path.is_file():
            raise DatabaseError(f"There is no SQLite file at {path}.")
        # mode=ro: SQLite itself refuses every write. query_only is the same lock once more.
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=self.timeout, check_same_thread=False)
        con.execute("PRAGMA query_only = ON")
        deadline = time.monotonic() + self.timeout
        con.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
        return con

    def _timed_out(self, exc: BaseException) -> bool:
        return isinstance(exc, sqlite3.OperationalError) and "interrupted" in str(exc)

    def _tables_sql(self):
        return (
            "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name",
            (),
        )

    def _schema_sql(self, table):
        return 'SELECT name, type, "notnull" AS not_null, dflt_value AS "default", pk FROM pragma_table_info(?)', (table,)


class Postgres(Connector):
    engine = "postgresql"

    def _open(self):
        import psycopg

        c = self.connection
        schema = c.schema or "public"
        return psycopg.connect(
            host=c.host,
            port=c.port or 5432,
            user=c.user,
            password=c.password,
            dbname=c.database,
            connect_timeout=CONNECT_TIMEOUT,
            sslmode="require" if c.ssl else "prefer",
            # Read-only and time-limited from the first statement on; the schema's tables need no prefix.
            options=f"-c default_transaction_read_only=on -c statement_timeout={self.timeout * 1000} -c search_path={schema}",
        )

    def _begin(self, con):
        con.read_only = True

    def _execute(self, cur, sql, params):
        # Without parameters psycopg uses PostgreSQL's simple protocol, which runs every
        # statement in the text ("SELECT 1; COMMIT; ..."). A prepared statement holds one.
        cur.execute(sql, params or None, prepare=True)

    def _timed_out(self, exc):
        return "statement timeout" in str(exc)

    def _tables_sql(self):
        return (
            "SELECT table_name, table_type FROM information_schema.tables WHERE table_schema = %s ORDER BY table_name",
            (self.connection.schema or "public",),
        )

    def _schema_sql(self, table):
        return (
            "SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
            (self.connection.schema or "public", table),
        )


class MySQL(Connector):
    engine = "mysql"

    def _open(self):
        import pymysql

        c = self.connection
        return pymysql.connect(
            host=c.host,
            port=c.port or 3306,
            user=c.user,
            password=c.password,
            database=c.database,
            charset="utf8mb4",
            connect_timeout=CONNECT_TIMEOUT,
            read_timeout=self.timeout + 5,
            ssl={"check_hostname": False} if c.ssl else None,
            autocommit=False,
        )

    def _begin(self, con):
        cur = con.cursor()
        # MySQL stops a SELECT after MAX_EXECUTION_TIME (ms), MariaDB after max_statement_time (s).
        for setting in (f"SET SESSION MAX_EXECUTION_TIME = {self.timeout * 1000}",
                        f"SET SESSION max_statement_time = {self.timeout}"):
            try:
                cur.execute(setting)
                break
            except Exception:
                continue
        # The session as well as this transaction: a later transaction is read-only too.
        cur.execute("SET SESSION TRANSACTION READ ONLY")
        cur.execute("START TRANSACTION READ ONLY")

    def _timed_out(self, exc):
        text = str(exc).lower()
        return "max_execution_time" in text or "max_statement_time" in text or "timed out" in text

    def _tables_sql(self):
        return (
            "SELECT table_name AS table_name, table_type AS table_type FROM information_schema.tables "
            "WHERE table_schema = DATABASE() ORDER BY table_name",
            (),
        )

    def _schema_sql(self, table):
        return (
            "SELECT column_name AS column_name, column_type AS column_type, is_nullable AS is_nullable, "
            "column_key AS column_key, column_default AS column_default, extra AS extra "
            "FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = %s "
            "ORDER BY ordinal_position",
            (table,),
        )


_CLASSES: dict[str, type[Connector]] = {"sqlite": SQLite, "postgresql": Postgres, "mysql": MySQL}


def connector(connection: Connection, timeout: int = 30) -> Connector:
    """AnythingLLM's `getDBClient`."""
    if connection.engine not in _CLASSES:
        raise DatabaseError(f"Unknown kind of database '{connection.engine}'.")
    driver = missing_driver(connection.engine)
    if driver:
        raise DatabaseError(
            f"{ENGINES[connection.engine]} needs the Python package {driver}: run "
            "`pip install -r requirements.txt`, or `cd backend && uv sync --inexact`, and restart."
        )
    return _CLASSES[connection.engine](connection, timeout)


def check_fields(connection: Connection) -> None:
    """Refuses a connection that is missing what its kind needs, before trying to reach it."""
    if connection.engine not in ENGINES:
        raise DatabaseError(f"Unknown kind of database '{connection.engine}'.")
    if connection.engine == "sqlite":
        if not connection.path.strip():
            raise DatabaseError("Give the path of the SQLite file on this machine.")
        return
    missing = [name for name in ("host", "user", "database") if not getattr(connection, name).strip()]
    if missing:
        raise DatabaseError("Fill in " + ", ".join(missing) + ".")
    if connection.port is not None and not 0 < connection.port < 65536:
        raise DatabaseError("The port must be a number from 1 to 65535.")
    if connection.schema and not _NAME.match(connection.schema):
        raise DatabaseError("The schema must be a plain name: letters, digits and underscores.")
