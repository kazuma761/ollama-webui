"""What the chat model is told when a workspace is picked. Edit here only."""

from __future__ import annotations

from .index import Hit

RULES = """\
You are answering from the documents in the workspace "{name}".{instructions}
Each question comes with numbered passages from those documents. Base the answer on them, and after each
statement that comes from a passage cite it by its number in square brackets, like [2]. If the passages do not
contain the answer, say that the workspace's documents do not cover it; do not make an answer up. Quote
figures, dates and names exactly as the passages give them."""

PASSAGES = """\
Passages from the workspace "{name}" that match this question:

{passages}"""

NOTHING = """\
No passage in the workspace "{name}" matched this question. Say so plainly, suggest how the person could
rephrase it or which document might be missing, and do not make an answer up."""


def where(hit: Hit) -> str:
    return f"{hit.document}, {hit.label}" if hit.label else hit.document


def rules(name: str, instructions: str) -> str:
    extra = f"\nThe workspace's own instructions: {instructions.strip()}" if instructions.strip() else ""
    return RULES.format(name=name, instructions=extra)


def passages(name: str, hits: list[Hit], start: int = 1) -> str:
    if not hits:
        return NOTHING.format(name=name)
    listed = "\n\n".join(f"[{i}] {where(h)}\n{h.text}" for i, h in enumerate(hits, start))
    return PASSAGES.format(name=name, passages=listed)


def system(name: str, instructions: str, hits: list[Hit]) -> str:
    """Rules and passages together, for a caller that sends them in one message."""
    return rules(name, instructions) + "\n\n" + passages(name, hits)


# ── Files attached in the chat (chatfiles.py) ──

FILES_WHOLE = """\
The files the user attached, whole:

{files}"""

FILES_PASSAGES = """\
The files the user attached are too long to read whole here, so these are the passages of them that match
this question.{whole} Cite a passage by its number in square brackets, like [3]. If the answer is not in them,
say that these passages do not cover it, and that asking for a summary reads the whole file.

{passages}"""

NOTES_SYSTEM = "You take careful notes from long documents, one part at a time."

NOTES = """\
This is part {part} of {parts} of "{name}"{where}:

{text}

Write the key points of this part as short bullet notes: what it is about, and every figure, date, name,
amount, decision and obligation in it, exactly as written. Do not add anything that is not in the part, and
do not write an introduction."""

COMBINE = """\
These are notes on consecutive parts of "{name}":

{notes}

Combine them into one shorter set of bullet notes that keeps every figure, date, name and decision, exactly as
written, in the order of the document. Do not add anything."""

FILE_NOTES = """\
Notes on the whole of "{name}", read in {parts} part{s}{skipped}. Base the answer on these notes:

{notes}"""
