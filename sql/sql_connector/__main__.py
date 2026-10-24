"""A check of the SQL connector without a model or a database server:  cd backend && python -m sql_connector"""

import asyncio
import json
import sqlite3
import tempfile
from pathlib import Path

from .connectors import Connection, DatabaseError, Postgres, check_fields, connector
from .sheets import SheetError, as_markdown, describe, import_file
from .safety import UnsafeQuery, check
from .store import ConnectionStore
from .tools import SqlTools

READS = [  # (query, the database it is read for)
    ("SELECT * FROM staff WHERE first_name LIKE 'sam%'", "postgresql"),
    ("select count(*) from orders;", "sqlite"),
    ("WITH t AS (SELECT 1 AS n) SELECT n FROM t", "postgresql"),
    ("SELECT 'drop table x; delete' AS note", "postgresql"),
    ("SELECT owner, comment, status, replace(name, 'a', 'b') FROM pets", "mysql"),
    ("SELECT 5 # 3", "postgresql"),  # XOR in PostgreSQL, not a comment
    ("SELECT 'it''s', 'a\\' FROM t", "postgresql"),
    ("SELECT 'it\\'s' FROM t", "mysql"),
    ("EXPLAIN SELECT 1", "sqlite"),
    ("SHOW TABLES", "mysql"),
]
WRITES = [
    # Each of the next three passed before 20 October 2026: the check read strings and comments the MySQL way.
    ("SELECT 1 # x; COMMIT; SET default_transaction_read_only = off; DELETE FROM orders", "postgresql"),
    ("SELECT 'a\\' ; DELETE FROM orders; --'", "postgresql"),
    ("SELECT 1--1 INTO OUTFILE '/tmp/x'", "mysql"),
    ("SELECT 1 -- a comment", "sqlite"),
    "DELETE FROM orders",
    "drop table orders",
    "SELECT 1; DELETE FROM orders",
    "WITH gone AS (DELETE FROM orders RETURNING *) SELECT * FROM gone",
    "EXPLAIN ANALYZE UPDATE orders SET total = 0",
    "SELECT * INTO backup FROM orders",
    "SELECT * FROM orders INTO OUTFILE '/tmp/x'",
    "SELECT pg_sleep(100)",
    "SELECT nextval('orders_id_seq')",
    "SELECT * FROM orders FOR UPDATE",
    "PRAGMA writable_schema = 1",
    "ATTACH DATABASE 'x.db' AS x",
    "/*!50000 DROP TABLE orders */ SELECT 1",
    "SELECT 'unclosed",
    "",
]


def _sample(folder: Path) -> Path:
    path = folder / "shop.db"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, city TEXT);
        CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER, total REAL, placed TEXT);
        INSERT INTO customers VALUES (1, 'Asha', 'Pune'), (2, 'Ravi', 'Mysuru'), (3, 'Meera', 'Pune');
        INSERT INTO orders VALUES (1, 1, 1200.5, '2026-09-01'), (2, 1, 300, '2026-09-12'), (3, 2, 999, '2026-10-02');
        CREATE VIEW totals AS SELECT customer_id, SUM(total) AS spent FROM orders GROUP BY customer_id;
        """
    )
    con.close()
    return path


def main() -> None:
    for sql, engine in READS:
        check(sql, engine)
    for item in WRITES:
        sql, engine = item if isinstance(item, tuple) else (item, "postgresql")
        try:
            check(sql, engine)
        except UnsafeQuery:
            continue
        raise AssertionError(f"not refused: {sql!r}")

    # PostgreSQL runs every statement of a text sent without parameters; a prepared statement holds one.
    class Cursor:
        def execute(self, sql, params=None, prepare=None):
            self.call = (sql, params, prepare)

    cur = Cursor()
    Postgres(Connection(id="pg", engine="postgresql"))._execute(cur, "SELECT 1", None)
    assert cur.call == ("SELECT 1", None, True), cur.call

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        path = _sample(folder)
        shop = Connection(id="shop", engine="sqlite", path=str(path))
        check_fields(shop)
        db = connector(shop, timeout=5)
        db.validate()
        assert [r[0] for r in db.list_tables().rows] == ["customers", "orders", "totals"]
        assert [r[0] for r in db.table_schema("customers").rows] == ["id", "name", "city"]
        try:
            db.table_schema("nope")
            raise AssertionError("a missing table was described")
        except DatabaseError:
            pass
        assert db.run("SELECT name FROM customers ORDER BY id", max_rows=2).truncated

        # The read-only lock on the connection holds even when the text check is skipped.
        try:
            db.run("DELETE FROM orders")
            raise AssertionError("SQLite let a write through")
        except DatabaseError:
            pass
        assert db.run("SELECT COUNT(*) FROM orders").rows == [[3]]

        # The tools, as the model calls them.
        tools = SqlTools([shop], max_rows=50, timeout=5)
        text, event = asyncio.run(tools.run("list_databases", {}))
        assert json.loads(text)["databases"][0]["database_id"] == "shop"
        text, event = asyncio.run(tools.run("list_tables", {"database_id": "shop"}))
        assert event["rows"] == 3
        text, event = asyncio.run(tools.run("get_table_schema", '{"database_id": "shop", "table_name": "orders"}'))
        assert "customer_id" in text
        text, event = asyncio.run(tools.run("query", {"sql_query": "SELECT c.city, SUM(o.total) AS spent FROM orders o "
                                                                     "JOIN customers c ON c.id = o.customer_id "
                                                                     "GROUP BY c.city ORDER BY spent DESC"}))
        assert json.loads(text)["rows"] == [["Pune", 1500.5], ["Mysuru", 999.0]] and event["sql"]
        text, event = asyncio.run(tools.run("query", {"database_id": "shop", "sql_query": "DROP TABLE orders"}))
        assert text.startswith("Error:") and event["error"]
        text, event = asyncio.run(tools.run("query", {"database_id": "other", "sql_query": "SELECT 1"}))
        assert "No database 'other'" in text
        assert "`shop`" in tools.system_prompt()

        # Saved connections: the password never reaches the page, and an edit without one keeps it.
        store = ConnectionStore(folder / "sql")
        saved = store.save(ConnectionStore.build({"name": "Sales DB", "engine": "postgresql", "host": "db",
                                                  "user": "reader", "password": "s3cret", "database": "sales"}))
        assert saved.id == "sales-db" and saved.public()["password"] is True
        assert (store.file.stat().st_mode & 0o777) == 0o600
        edited = ConnectionStore.build({"name": "Sales DB", "engine": "postgresql", "host": "db2",
                                        "user": "reader", "database": "sales"})
        store.save(edited, original_id="sales-db")
        assert store.get("sales-db").password == "s3cret" and store.get("sales-db").host == "db2"
        try:
            store.save(ConnectionStore.build({"name": "sales_db", "engine": "sqlite", "path": str(path)}))
            raise AssertionError("a second connection with the same id was saved")
        except DatabaseError:
            pass
        assert store.delete("sales-db") and store.all() == []

    # Spreadsheets into tables: a title above the header, Indian number commas, ₹, day-first dates,
    # PIN codes with leading zeros, an empty sheet; then a total by SQL instead of by reading.
    with tempfile.TemporaryDirectory() as tmp:
        csv_data = ("Sales report Q1,,,\n,,,\nRegion,Month,Amount (₹),PIN\n"
                    "North,31/01/2026,\"1,23,456.50\",011001\nSouth,28/02/2026,₹500,560001\n").encode()
        tables = import_file("sales.csv", csv_data, Path(tmp) / "csv.db")
        columns = {c["name"]: c["type"] for c in tables[0]["columns"]}
        assert columns == {"region": "TEXT", "month": "DATE", "amount": "REAL", "pin": "TEXT"}, columns
        assert tables[0]["title"] == "Sales report Q1" and tables[0]["rows"] == 2
        tools = SqlTools([Connection(id="sales.csv", engine="sqlite", path=str(Path(tmp) / "csv.db"))],
                         about={"sales.csv": describe(tables, "sales.csv")})
        assert "amount REAL" in tools.system_prompt()
        text, event = asyncio.run(tools.run("query", {"sql_query": "SELECT SUM(amount), MIN(month), MAX(pin) FROM sales"}))
        assert json.loads(text)["rows"] == [[123956.5, "2026-01-31", "560001"]], text

        from openpyxl import Workbook
        import datetime
        import io
        book = Workbook()
        sheet = book.active
        sheet.title = "Invoices 2026"
        sheet.append(["ACME Pvt Ltd - invoice register"])
        sheet.append([])
        sheet.append(["Invoice No", "Date", "Customer", "Total"])
        for i in range(3000):
            sheet.append([f"INV-{i:04d}", datetime.datetime(2026, 1 + i % 12, 1 + i % 28), ["Asha", "Ravi"][i % 2], 100 + i])
        book.create_sheet("Empty")
        data = io.BytesIO()
        book.save(data)
        tables = import_file("register.xlsx", data.getvalue(), Path(tmp) / "xlsx.db")
        assert [t["table"] for t in tables] == ["invoices_2026"] and tables[0]["rows"] == 3000
        db = sqlite3.connect(Path(tmp) / "xlsx.db")
        assert db.execute("SELECT customer, COUNT(*), SUM(total) FROM invoices_2026 GROUP BY customer").fetchall() == \
            [("Asha", 1500, 1500 * 100 + sum(range(0, 3000, 2))), ("Ravi", 1500, 1500 * 100 + sum(range(1, 3000, 2)))]
        db.close()
        small = import_file("t.csv", b"Name,Total\nA,1\nB,2\n", Path(tmp) / "t.db")
        assert as_markdown(Path(tmp) / "t.db", small).splitlines()[1:4] == ["| name | total |", "| --- | --- |", "| A | 1 |"]
        try:
            import_file("notes.txt", b"hello", Path(tmp) / "x.db")
            raise AssertionError("a text file was taken as a spreadsheet")
        except SheetError:
            pass

    print("SQL connector: query checks, SQLite, read-only lock, tools, saved connections and spreadsheets all pass.")


if __name__ == "__main__":
    main()
