"""A document's text -> passages of about `size` characters. No model here.

A passage ends at a paragraph break where it can, else at the end of a
sentence, else at a space, so it rarely stops mid-word. Each one repeats the
last `overlap` characters of the one before, so a sentence that falls on a
boundary is whole in one of them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_BREAKS = (re.compile(r"\n\s*\n"), re.compile(r"(?<=[.!?:;])\s+"), re.compile(r"\s+"))


@dataclass
class Passage:
    text: str
    label: str  # where it is in the document: "page 4", "sheet Sales", or "" for a plain file


def _cut(text: str, size: int) -> int:
    """Where to end a passage that starts at 0: the last good break before `size`."""
    if len(text) <= size:
        return len(text)
    window = text[:size]
    for pattern in _BREAKS:
        ends = [m.end() for m in pattern.finditer(window) if m.end() >= size // 2]
        if ends:
            return ends[-1]
    return size


def split(text: str, size: int = 1200, overlap: int = 200) -> list[str]:
    text = re.sub(r"[ \t]+\n", "\n", text).strip()
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = start + _cut(text[start:], size)
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(text):
            break
        # Step back by the overlap, but to a word boundary, and always forward.
        back = max(start + 1, end - overlap)
        space = text.find(" ", back, end)
        start = space + 1 if 0 <= space < end else back
    return pieces


def passages(sections: list[tuple[str, str]], size: int = 1200, overlap: int = 200) -> list[Passage]:
    """Sections are (label, text): one per PDF page, or one for the whole file."""
    out: list[Passage] = []
    for label, text in sections:
        out.extend(Passage(piece, label) for piece in split(text, size, overlap))
    return out
