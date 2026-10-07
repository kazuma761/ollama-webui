"""Where the documents page keeps files on disk.

  Store    uploads and the versions made from them, one folder per upload under an
           unguessable id; deleted after `documents.keep_hours`.
  Library  prepared templates with their labels and slot map; kept until deleted.

Both live under backend/data/, which is not in git.
"""

import json
import re
import secrets
import shutil
import time
from pathlib import Path

from document_engine import slotmap_summary
from fastapi import HTTPException

_JOB = re.compile(r"^[A-Za-z0-9_-]{20,40}$")


class Store:
    """Uploaded files and the versions made from them, one folder per upload."""

    def __init__(self, root: Path, keep_hours: int):
        self.root, self.keep_seconds = root, keep_hours * 3600
        root.mkdir(parents=True, exist_ok=True)
        self.cleanup()

    def cleanup(self) -> None:
        for folder in self.root.iterdir():
            if folder.is_dir() and _JOB.match(folder.name):
                newest = max((f.stat().st_mtime for f in folder.iterdir()), default=folder.stat().st_mtime)
                if time.time() - newest > self.keep_seconds:
                    shutil.rmtree(folder, ignore_errors=True)

    def _folder(self, job: str) -> Path:
        folder = self.root / job
        if not _JOB.match(job) or not folder.is_dir():
            raise HTTPException(404, "That document is no longer on the server. Upload it again.")
        return folder

    def create(self, name: str, data: bytes, label: str = "Uploaded") -> str:
        self.cleanup()
        job = secrets.token_urlsafe(18)
        folder = self.root / job
        folder.mkdir()
        (folder / "v0.docx").write_bytes(data)
        (folder / "meta.json").write_text(json.dumps({"name": name, "versions": [label]}))
        return job

    def meta(self, job: str) -> dict:
        return json.loads((self._folder(job) / "meta.json").read_text())

    def read(self, job: str, version: int | None = None) -> tuple[str, bytes, int]:
        meta = self.meta(job)
        latest = len(meta["versions"]) - 1
        version = latest if version is None else version
        if not 0 <= version <= latest:
            raise HTTPException(404, "That version does not exist.")
        return meta["name"], (self._folder(job) / f"v{version}.docx").read_bytes(), version

    def add(self, job: str, data: bytes, label: str) -> int:
        folder, meta = self._folder(job), self.meta(job)
        meta["versions"].append(label)
        version = len(meta["versions"]) - 1
        (folder / f"v{version}.docx").write_bytes(data)
        (folder / "meta.json").write_text(json.dumps(meta))
        return version


class Library:
    """Prepared templates. Unlike uploads these are kept until someone deletes them."""

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def folder(self, template: str) -> Path:
        folder = self.root / template
        if not _JOB.match(template) or not folder.is_dir():
            raise HTTPException(404, "That template is not in the library.")
        return folder

    def create(self, name: str, data: bytes) -> str:
        template = secrets.token_urlsafe(18)
        folder = self.root / template
        folder.mkdir()
        (folder / "template.docx").write_bytes(data)
        title = re.sub(r"\.(docx|dotx)$", "", name, flags=re.IGNORECASE)
        self.write(template, "meta", {"name": title, "file": name, "created": time.strftime("%Y-%m-%d")})
        return template

    def read(self, template: str, part: str) -> dict | None:
        path = self.folder(template) / f"{part}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def write(self, template: str, part: str, value: dict) -> None:
        (self.folder(template) / f"{part}.json").write_text(json.dumps(value))

    def word(self, template: str) -> tuple[str, bytes]:
        return self.read(template, "meta")["file"], (self.folder(template) / "template.docx").read_bytes()

    def entries(self) -> list[dict]:
        found = []
        for folder in sorted(self.root.iterdir()):
            if folder.is_dir() and _JOB.match(folder.name) and (folder / "meta.json").exists():
                slotmap = self.read(folder.name, "slotmap")
                found.append({
                    "id": folder.name, **self.read(folder.name, "meta"),
                    "ready": slotmap is not None, "summary": slotmap_summary(slotmap) if slotmap else None,
                })
        return sorted(found, key=lambda entry: entry["name"].lower())
