"""Turns an attached file (PDF, DOCX or plain text) into prompt text."""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import PurePath
from xml.etree import ElementTree

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from .errors import PipelineError

MAX_FILE_CHARS = 200_000

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class UnsupportedDocument(PipelineError):
    """The file can't be turned into text."""


def _pdf(name: str, data: bytes) -> str:
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise UnsupportedDocument(f"{name} is password-protected")
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    except (PyPdfError, ValueError, KeyError) as exc:
        raise UnsupportedDocument(f"{name} could not be read as a PDF") from exc


def _docx(name: str, data: bytes) -> str:
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            body = ElementTree.fromstring(archive.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise UnsupportedDocument(f"{name} could not be read as a Word document") from exc

    paragraphs = []
    for paragraph in body.iter(f"{_W}p"):
        parts = []
        for element in paragraph.iter():
            if element.tag == f"{_W}t":
                parts.append(element.text or "")
            elif element.tag == f"{_W}tab":
                parts.append("\t")
            elif element.tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
        paragraphs.append("".join(parts))
    return "\n".join(paragraphs)


def _plain(name: str, data: bytes) -> str:
    if b"\x00" in data:
        raise UnsupportedDocument(f"{name} is not a supported type (text, PDF and DOCX work)")
    return data.decode("utf-8", errors="replace")


def extract_text(name: str, data: bytes) -> tuple[str, bool]:
    """Returns (text, truncated). Raises UnsupportedDocument with a user-facing reason."""
    suffix = PurePath(name).suffix.lower()
    if suffix == ".pdf" or data.startswith(b"%PDF-"):
        text = _pdf(name, data)
        if not text.strip():
            raise UnsupportedDocument(f"{name} has no selectable text (a scanned PDF?)")
    elif suffix == ".docx":
        text = _docx(name, data)
    else:
        text = _plain(name, data)

    text = text.strip()
    if not text:
        raise UnsupportedDocument(f"{name} is empty")
    return text[:MAX_FILE_CHARS], len(text) > MAX_FILE_CHARS
