"""Which SQL the model may run: one statement that only reads.

This is the first of four locks. The others are on the connection itself
(see `connectors.py`): every driver is made to run exactly one statement per
call, SQLite is opened read-only, and PostgreSQL and MySQL run every query in
a read-only transaction. A database user that can only read is the last, and
the one to rely on: set it up on the server.

AnythingLLM only asks its model, in the tool's description, to send SELECTs.
Here the request is refused before it reaches the database.
"""

from __future__ import annotations

import re


class UnsafeQuery(ValueError):
    """The statement could change the database, or is not one statement."""


# A statement may start with these. SHOW, DESCRIBE and EXPLAIN are MySQL's ways to look around.
_STARTS = {"select", "with", "show", "describe", "desc", "explain", "values", "table"}

# Words that write, lock, wait, or reach outside the database from inside a
# statement that starts like a read. Checked anywhere outside strings and
# comments, so that a write hidden in a CTE (`WITH x AS (DELETE ...)`) or
# behind EXPLAIN ANALYZE is caught. Statements that start with a write
# (DROP, SET, ATTACH, PRAGMA ...) are already refused by `_STARTS`; the list
# stays short so that ordinary column names (owner, comment, status) pass.
_FORBIDDEN = {
    "insert", "update", "delete", "merge", "upsert", "truncate", "create", "alter", "drop",
    "grant", "revoke", "copy", "call", "exec", "execute", "lock",
    "into",  # SELECT ... INTO makes a table (PostgreSQL), a variable or a file (MySQL)
    "outfile", "dumpfile", "load_file",
    "nextval", "setval", "set_config", "pg_sleep", "sleep", "benchmark",
    "dblink", "dblink_exec", "lo_import", "lo_export", "pg_read_file", "pg_read_binary_file",
    "pg_ls_dir", "pg_stat_file", "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf",
    "pg_advisory_lock", "pg_advisory_xact_lock", "query_to_xml", "query_to_xml_and_xmlschema",
}

# Strings and quoted names, removed before the words are checked. They are read
# the way each database reads them: a backslash escapes a quote in MySQL only,
# and $$dollar strings$$ exist in PostgreSQL only. Reading them any other way
# would let a database see a second statement where this check sees a string.
_PLAIN_STRING = r"'(?:[^']|'')*'"
_MYSQL_STRING = r"'(?:[^'\\]|\\.|'')*'|\"(?:[^\"\\]|\\.|\"\")*\""
_NAME = r'"(?:[^"]|"")*"'
_LITERALS = {
    "mysql": re.compile("|".join([_MYSQL_STRING, r"`(?:[^`]|``)*`"]), re.DOTALL),
    "postgresql": re.compile("|".join([_PLAIN_STRING, _NAME, r"\$(\w*)\$.*?\$\1\$"]), re.DOTALL),
    "sqlite": re.compile("|".join([_PLAIN_STRING, _NAME, r"`(?:[^`]|``)*`", r"\[[^\]]*\]"]), re.DOTALL),
}
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")


def check(sql: str, engine: str = "postgresql") -> str:
    """Returns the statement without a trailing semicolon, or raises UnsafeQuery.

    `engine` is "sqlite", "postgresql" or "mysql": the strings are read as that
    database reads them. Comments are refused rather than read: each database
    has its own idea of where one ends (`#` is a comment in MySQL but XOR in
    PostgreSQL; `--1` is a comment here but "minus minus one" in MySQL), and
    a query does not need them.
    """
    text = (sql or "").strip()
    if not text:
        raise UnsafeQuery("The query is empty.")
    bare = _LITERALS.get(engine, _LITERALS["postgresql"]).sub(" ", text)
    if "'" in bare or '"' in bare or "`" in bare:
        raise UnsafeQuery("The query has a string or a quoted name that is not closed.")
    if "--" in bare or "/*" in bare or "*/" in bare or (engine == "mysql" and "#" in bare):
        raise UnsafeQuery("Remove the comments from the query and send it again.")

    body = bare.strip().rstrip(";").strip()
    if ";" in body:
        raise UnsafeQuery("Only one statement can be run at a time.")
    words = [w.lower() for w in _WORD.findall(body)]
    if not words or words[0] not in _STARTS:
        raise UnsafeQuery("Only queries that read are allowed: SELECT, WITH, SHOW, DESCRIBE or EXPLAIN.")
    found = sorted(set(words) & _FORBIDDEN)
    if found:
        raise UnsafeQuery(f"Only queries that read are allowed; this one uses {', '.join(w.upper() for w in found)}.")
    return text.rstrip().rstrip(";").rstrip()
