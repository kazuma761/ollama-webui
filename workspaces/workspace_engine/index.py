"""One workspace on disk: its settings and its search index. No model here.

  <root>/<id>/workspace.json   name, instructions, which embedding model made the vectors
  <root>/<id>/index.db         documents, their passages, the passages' vectors,
                               and a full-text index of the passages (SQLite FTS5)

Searching combines two rankings (reciprocal rank fusion): passages that share
words with the question (FTS5's BM25), and passages whose meaning is closest
(the dot product of unit vectors). Without vectors, words alone decide.
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import sqlite3
import threading
import time
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .chunking import Passage

_ID = re.compile(r"^[a-z0-9-]{3,60}$")
_TOKEN = re.compile(r"\w{2,}", re.UNICODE)
# Words that say nothing about which passage is meant.
_STOP = set(
    "the a an and or of to in on for with from by at as is are was were be been it its this that these those "
    "what which who whom whose when where why how do does did can could should would will shall may might must "
    "i me my we our you your he she they them their his her about into than then there here please tell give "
    "show find list explain say all any some no not yes".split()
)
RRF_K = 60  # the usual constant of reciprocal rank fusion: how much a top rank counts over a lower one
CANDIDATES = 30  # passages taken from each ranking before they are combined

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    added TEXT NOT NULL,
    chars INTEGER NOT NULL,
    pages INTEGER,
    truncated INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    vector BLOB
);
CREATE INDEX IF NOT EXISTS chunks_document ON chunks(document_id);
"""

FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, content='chunks', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS chunks_added AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_removed AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
"""


class WorkspaceError(Exception):
    """Something the person can act on: a missing workspace, a duplicate, a bad name."""


@dataclass
class Hit:
    chunk_id: int
    document: str
    label: str
    position: int
    text: str
    score: float
    matched_by: str  # "words", "meaning" or "both"


def slug(name: str) -> str:
    """The id part of a name: plain ASCII, so it fits in a URL and a folder name anywhere.

    Accents are dropped (año -> ano); a name in another script (ಮಾನವ ಸಂಪನ್ಮೂಲ)
    has no letters left and gives "", so the id is then "workspace-<random>".
    The name people see keeps every letter.
    """
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9\s_-]", "", text).strip()
    return re.sub(r"[\s_-]+", "-", text).strip("-")[:40].strip("-")


def keywords(question: str) -> list[str]:
    words = [w.lower() for w in _TOKEN.findall(question)]
    return list(dict.fromkeys(w for w in words if w not in _STOP)) or list(dict.fromkeys(words))


class Workspace:
    """One workspace's folder. Writes are serialised by a lock; reads open their own connection."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.id = folder.name
        self._lock = threading.Lock()
        self._vectors: tuple[np.ndarray, np.ndarray] | None = None  # (chunk ids, matrix), until the index changes
        with self._db() as db:
            db.executescript(SCHEMA)
            try:
                db.executescript(FTS)
                self.fts = True
            except sqlite3.OperationalError:
                self.fts = False  # this Python's SQLite was built without FTS5: words are matched in Python

    # ── settings ──
    @property
    def meta_file(self) -> Path:
        return self.folder / "workspace.json"

    def meta(self) -> dict[str, Any]:
        return json.loads(self.meta_file.read_text(encoding="utf-8"))

    def save_meta(self, **changes: Any) -> dict[str, Any]:
        meta = {**self.meta(), **changes}
        temp = self.meta_file.with_suffix(".tmp")
        temp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        temp.replace(self.meta_file)
        return meta

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        """A connection that commits when the block ends well, and is always closed."""
        db = sqlite3.connect(self.folder / "index.db", timeout=30)
        try:
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("PRAGMA journal_mode = WAL")
            with db:
                yield db
        finally:
            db.close()

    # ── documents ──
    def documents(self) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute(
                "SELECT d.id, d.name, d.added, d.chars, d.pages, d.truncated, COUNT(c.id), COUNT(c.vector) "
                "FROM documents d LEFT JOIN chunks c ON c.document_id = d.id GROUP BY d.id ORDER BY d.name COLLATE NOCASE"
            ).fetchall()
        return [
            {"id": r[0], "name": r[1], "added": r[2], "chars": r[3], "pages": r[4], "truncated": bool(r[5]),
             "passages": r[6], "with_vectors": r[7]}
            for r in rows
        ]

    def find(self, name: str, sha256: str) -> tuple[int | None, bool]:
        """The id of a document with this name, and whether its content is the same."""
        with self._db() as db:
            row = db.execute("SELECT id, sha256 FROM documents WHERE name = ?", (name,)).fetchone()
        return (row[0], row[1] == sha256) if row else (None, False)

    def add(self, name: str, sha256: str, chars: int, pages: int | None, truncated: bool,
            passages: list[Passage], vectors: list[np.ndarray | None]) -> int:
        """Stores a document and its passages, replacing one of the same name."""
        with self._lock, self._db() as db:
            db.execute("DELETE FROM documents WHERE name = ?", (name,))
            cur = db.execute(
                "INSERT INTO documents (name, sha256, added, chars, pages, truncated) VALUES (?, ?, ?, ?, ?, ?)",
                (name, sha256, time.strftime("%Y-%m-%d %H:%M"), chars, pages, int(truncated)),
            )
            document_id = cur.lastrowid
            db.executemany(
                "INSERT INTO chunks (document_id, position, label, text, vector) VALUES (?, ?, ?, ?, ?)",
                [
                    (document_id, i, p.label, p.text, v.astype(np.float32).tobytes() if v is not None else None)
                    for i, (p, v) in enumerate(zip(passages, vectors))
                ],
            )
        self._vectors = None
        return document_id

    def remove(self, document_id: int) -> bool:
        with self._lock, self._db() as db:
            removed = db.execute("DELETE FROM documents WHERE id = ?", (document_id,)).rowcount
        self._vectors = None
        return bool(removed)

    def passages_without_vectors(self, limit: int = 10_000) -> list[tuple[int, str]]:
        with self._db() as db:
            return db.execute("SELECT id, text FROM chunks WHERE vector IS NULL ORDER BY id LIMIT ?", (limit,)).fetchall()

    def set_vectors(self, pairs: list[tuple[int, np.ndarray]]) -> None:
        with self._lock, self._db() as db:
            db.executemany("UPDATE chunks SET vector = ? WHERE id = ?",
                           [(v.astype(np.float32).tobytes(), i) for i, v in pairs])
        self._vectors = None

    def clear_vectors(self) -> None:
        with self._lock, self._db() as db:
            db.execute("UPDATE chunks SET vector = NULL")
        self._vectors = None

    def counts(self) -> dict[str, int]:
        with self._db() as db:
            documents = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            passages, with_vectors = db.execute("SELECT COUNT(*), COUNT(vector) FROM chunks").fetchone()
        return {"documents": documents, "passages": passages, "with_vectors": with_vectors}

    # ── search ──
    def _by_words(self, db: sqlite3.Connection, words: list[str]) -> list[int]:
        if not words:
            return []
        if self.fts:
            query = " OR ".join('"' + w.replace('"', "") + '"' for w in words)
            rows = db.execute(
                "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT ?",
                (query, CANDIDATES),
            ).fetchall()
            return [r[0] for r in rows]
        # Without FTS5: count how many of the words each passage has.
        scored = []
        for chunk_id, text in db.execute("SELECT id, lower(text) FROM chunks"):
            found = sum(1 for w in words if w in text)
            if found:
                scored.append((-found, chunk_id))
        return [chunk_id for _, chunk_id in sorted(scored)[:CANDIDATES]]

    def _matrix(self) -> tuple[np.ndarray, np.ndarray]:
        """Every passage's vector, read once and kept until the index changes."""
        if self._vectors is None:
            with self._db() as db:
                rows = db.execute("SELECT id, vector FROM chunks WHERE vector IS NOT NULL").fetchall()
            sizes = {len(r[1]) for r in rows}
            if not rows or len(sizes) != 1:  # none, or from two different embedding models
                ids, matrix = np.zeros(0, dtype=np.int64), np.zeros((0, 0), dtype=np.float32)
            else:
                ids = np.array([r[0] for r in rows], dtype=np.int64)
                matrix = np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
            self._vectors = (ids, matrix)
        return self._vectors

    def _by_meaning(self, vector: np.ndarray | None) -> list[int]:
        if vector is None:
            return []
        ids, matrix = self._matrix()
        if not len(ids) or matrix.shape[1] != vector.shape[0]:
            return []
        scores = matrix @ vector
        best = np.argsort(-scores)[:CANDIDATES]
        return [int(ids[i]) for i in best]

    def search(self, question: str, vector: np.ndarray | None, top_k: int = 6) -> list[Hit]:
        with self._db() as db:
            by_words = self._by_words(db, keywords(question))
            by_meaning = self._by_meaning(vector)
            fused: dict[int, float] = {}
            for ranking in (by_words, by_meaning):
                for rank, chunk_id in enumerate(ranking):
                    fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank + 1)
            best = sorted(fused, key=lambda c: -fused[c])[:top_k]
            if not best:
                return []
            marks = ",".join("?" * len(best))
            rows = {
                r[0]: r
                for r in db.execute(
                    f"SELECT c.id, d.name, c.label, c.position, c.text FROM chunks c "
                    f"JOIN documents d ON d.id = c.document_id WHERE c.id IN ({marks})",
                    best,
                )
            }
        words, meaning = set(by_words), set(by_meaning)
        return [
            Hit(c, rows[c][1], rows[c][2], rows[c][3], rows[c][4], round(fused[c] * 1000, 2),
                "both" if c in words and c in meaning else "words" if c in words else "meaning")
            for c in best
            if c in rows
        ]


class Workspaces:
    """All workspaces under one folder (backend/data/workspaces/)."""

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self._open: dict[str, Workspace] = {}
        self._lock = threading.Lock()

    def get(self, workspace_id: str) -> Workspace:
        folder = self.root / workspace_id
        if not _ID.match(workspace_id or "") or not (folder / "workspace.json").exists():
            raise WorkspaceError(f"There is no workspace '{workspace_id}'. It may have been deleted.")
        with self._lock:
            if workspace_id not in self._open:
                self._open[workspace_id] = Workspace(folder)
            return self._open[workspace_id]

    def all(self) -> list[Workspace]:
        found = []
        for folder in sorted(self.root.iterdir()):
            if folder.is_dir() and _ID.match(folder.name) and (folder / "workspace.json").exists():
                found.append(self.get(folder.name))
        return found

    def create(self, name: str, instructions: str = "") -> Workspace:
        name = name.strip()
        if not name:
            raise WorkspaceError("Give the workspace a name, e.g. HR policies.")
        if any(w.meta()["name"].lower() == name.lower() for w in self.all()):
            raise WorkspaceError(f"A workspace named '{name}' already exists.")
        workspace_id = f"{slug(name) or 'workspace'}-{secrets.token_hex(3)}"
        folder = self.root / workspace_id
        folder.mkdir()
        try:
            (folder / "workspace.json").write_text(json.dumps({
                "id": workspace_id, "name": name[:80], "instructions": instructions.strip(),
                "created": time.strftime("%Y-%m-%d"), "embed_model": "",
            }, indent=2), encoding="utf-8")
            return self.get(workspace_id)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)  # no half-made workspace left behind, unseen
            raise

    def delete(self, workspace_id: str) -> None:
        workspace = self.get(workspace_id)
        with self._lock:
            self._open.pop(workspace_id, None)
        shutil.rmtree(workspace.folder)
