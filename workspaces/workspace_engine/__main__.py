"""A check of workspaces without a model:  cd backend && python -m workspace_engine

A stand-in embedding model (words hashed into a vector) shows that search by
meaning and by words combine; a missing one shows that words alone still work.
The same stand-in answers chat requests, for reading a long file in parts.
"""

import asyncio
import hashlib
import re
import tempfile
from dataclasses import replace
from pathlib import Path

from ollama_pipeline import ModelEntry, OllamaUnavailable, estimate, load_config

from .chatfiles import ChatFiles, wants_whole
from .chunking import split
from .embedding import Embedder
from .service import WorkspaceService

LEAVE = """Leave policy

Every employee gets 24 days of paid leave a year. Leave must be asked for in the HR portal at least two weeks ahead.

Sick leave is separate: up to 12 days a year with a doctor's note after the second day.

Parental leave is 26 weeks for the birth parent and 4 weeks for the other parent."""

TRAVEL = """Travel policy

Flights over 6 hours may be booked in premium economy. Hotels up to 9,000 rupees a night in metro cities.
Claims go in within 30 days of the trip, with receipts."""


def page(n: int) -> str:
    """One page of a long made-up contract; page 37 holds the one fact asked about."""
    lines = [f"Clause {n}.{k}. The supplier keeps the records of batch {n * 100 + k} in the register for the yearly audit."
             for k in range(1, 15)]
    if n == 37:
        lines.insert(7, "The penalty for late delivery is 2.5% of the order value per week.")
    return "\n".join(lines)


class StandIn:
    """An embedding 'model': each word sets one of 64 positions. Asked to take notes, it notes any line with a %."""

    def __init__(self, up: bool = True):
        self.up = up
        self.asked = 0

    async def chat_stream(self, model, messages, options=None, tools=None):
        self.asked += 1
        found = next((line.strip().lstrip("- ") for line in messages[-1]["content"].splitlines() if "%" in line), "")
        yield {"message": {"content": f"- {found or 'Routine clauses on keeping records.'}"}}
        yield {"done": True}

    async def embed(self, model, texts):
        if not self.up:
            raise OllamaUnavailable("not running")
        vectors = []
        for text in texts:
            v = [0.0] * 64
            for word in re.findall(r"\w+", text.lower()):
                v[int(hashlib.md5(word.encode()).hexdigest(), 16) % 64] += 1.0
            vectors.append(v)
        return vectors


class Registry:
    def __init__(self, client):
        config = load_config()
        self.config = replace(config, workspaces=replace(config.workspaces, embed_model="stand-in", query_prefix="", document_prefix=""))
        self._client = client

    def client(self, host):
        return self._client


async def collect(stream):
    return [event async for event in stream]


async def chat_files(root: Path) -> None:
    """Files attached in the chat: whole when they fit, passages when not, read in parts for a summary."""
    stand_in = StandIn()
    registry = Registry(stand_in)
    files = ChatFiles(registry, root, Embedder(registry), keep_hours=24)
    assert wants_whole("Can you summarise this document?") and wants_whole("give me the key points of the pdf")
    assert not wants_whole("What is the penalty for late delivery?")

    small_id, folder = files.new()
    small = await files.add(small_id, folder, "leave.txt", LEAVE.encode(), [("", LEAVE)])
    block, event = await files.context([small], "How many days of sick leave?", 4000)
    assert event is None and '<file name="leave.txt">' in block and "12 days a year" in block

    big_id, folder = files.new()
    pages = [(f"page {n}", page(n)) for n in range(1, 61)]
    big = await files.add(big_id, folder, "contract.pdf", b"%PDF stand-in", pages, pages=60)
    assert big.tokens > 15_000 and big.vectors == "pending", big
    await asyncio.gather(*files._tasks)  # vectors are made after the upload has answered
    big = files.get(big_id)
    assert big.vectors == "ready" and big.public()["pages"] == 60 and "text" not in big.public()

    # Too long to give whole: the passage with the fact is found, and the small file still goes whole.
    block, event = await files.context([big, small], "What is the penalty for late delivery?", 3000)
    assert estimate(block) <= 3200, estimate(block)
    assert "2.5% of the order value" in block and '<file name="leave.txt">' in block
    assert event["items"][0]["label"] == "page 37" and event["mode"] == "meaning and words", event["items"][:2]

    # A summary reads every part, notes each, and keeps the notes for the next question.
    entry = ModelEntry(id="general", label="Stand-in", model="stand-in", host="local")
    parts = files.parts(big, 3000)
    assert len(parts) > 3 and parts[0][0].startswith("pages 1-") and all(estimate(t) <= 3000 for _, t in parts)
    events = [e async for e in files.read_whole(big, entry, 3000, 1500)]
    progress, notes = events[:-1], events[-1]
    assert progress[0]["done"] == 0 and progress[-1] == {"type": "progress", "done": len(parts), "total": len(parts)}
    assert notes["type"] == "notes" and notes["parts"] == len(parts) and "2.5% of the order value" in notes["text"]
    assert "Part 1 (pages 1-" in notes["text"] and stand_in.asked == len(parts)
    events = [e async for e in files.read_whole(big, entry, 3000, 1500)]
    assert events[0]["done"] == len(parts) and stand_in.asked == len(parts)  # read once, not twice
    # Notes too long together are combined into fewer, keeping the figures.
    events = [e async for e in files.read_whole(big, entry, 3000, 20)]
    assert stand_in.asked == len(parts) + 1 and "2.5% of the order value" in events[-1]["text"]

    # Without an embedding model: words only, and the start of the file when no word matches.
    down = ChatFiles(Registry(StandIn(up=False)), root, Embedder(Registry(StandIn(up=False))), keep_hours=24)
    plain_id, folder = down.new()
    plain = await down.add(plain_id, folder, "plain.pdf", b"%PDF other", pages, pages=60)
    assert plain.vectors == "none"
    block, event = await down.context([plain], "zzzz qqqq", 1500)
    assert event["mode"] == "words" and event["items"][0]["label"] == "page 1"

    # Kept for a day: after that the file is gone, and cleaned away.
    files.keep_seconds = -1
    assert files.get(small_id) is None
    files.cleanup()
    assert not (root / small_id).exists() and not (root / big_id).exists()


def main() -> None:
    long = " ".join(f"Sentence number {i} is here." for i in range(400))
    pieces = split(long, 500, 100)
    assert all(len(p) <= 500 for p in pieces) and len(pieces) > 5
    last = pieces[0].rsplit(". ", 1)[-1]
    assert last in pieces[1] and f" {pieces[1].split()[0]} " in f" {long} "  # the overlap is kept, and starts at a word

    with tempfile.TemporaryDirectory() as tmp:
        service = WorkspaceService(Registry(StandIn()), Path(tmp))
        hr = service.create("HR policies", "Answer for employees in India.")
        try:
            service.create("hr policies")
            raise AssertionError("a second workspace with the same name was made")
        except Exception as exc:
            assert "already exists" in str(exc)

        # Names in any language: the id is ASCII, the name keeps its letters.
        for name in ("ಮಾನವ ಸಂಪನ್ಮೂಲ", "Finanzas año 2026"):
            made = service.create(name)
            assert made["name"] == name and service.store.get(made["id"]), made
        assert sorted(w["name"] for w in service.list()) == ["Finanzas año 2026", "HR policies", "ಮಾನವ ಸಂಪನ್ಮೂಲ"]
        for w in service.list():
            if w["id"] != hr["id"]:
                service.delete(w["id"])

        events = asyncio.run(collect(service.add(hr["id"], "leave.txt", LEAVE.encode(), [("", LEAVE)], False)))
        assert events[-1]["type"] == "added" and events[-1]["by_meaning"], events
        events = asyncio.run(collect(service.add(hr["id"], "travel.pdf", TRAVEL.encode(), [("page 1", TRAVEL)], False, 1)))
        assert events[-1]["passages"] == 1
        again = asyncio.run(collect(service.add(hr["id"], "leave.txt", LEAVE.encode(), [("", LEAVE)], False)))
        assert again[-1]["type"] == "skipped"

        hits, mode = asyncio.run(service.search(hr["id"], "How many days of sick leave do I get?"))
        assert mode == "meaning and words" and "Sick leave" in hits[0].text, [h.text[:40] for h in hits]
        hits, _ = asyncio.run(service.search(hr["id"], "hotel limit per night"))
        assert hits[0].document == "travel.pdf" and hits[0].label == "page 1"

        rules, passages, event = asyncio.run(service.context(hr["id"], [
            {"role": "user", "content": "What is the hotel limit in metro cities?"},
            {"role": "assistant", "content": "..."},
            {"role": "user", "content": "and for flights?"},
        ], max_tokens=2000, start=3))
        assert "[3] travel.pdf, page 1" in passages and "Answer for employees in India." in rules
        assert event["items"][0]["document"] == "travel.pdf" and event["items"][0]["n"] == 3
        rules, passages, event = asyncio.run(service.context(hr["id"], [{"role": "user", "content": ""}]))
        assert rules and not passages and event is None  # a picture alone: nothing to search for

        # The embedding model goes away: words still find passages, and new files are still added.
        down = WorkspaceService(Registry(StandIn(up=False)), Path(tmp))
        hits, mode = asyncio.run(down.search(hr["id"], "parental leave weeks"))
        assert mode == "words" and "Parental" in hits[0].text
        events = asyncio.run(collect(down.add(hr["id"], "notes.txt", b"Office opens at 9.", [("", "Office opens at 9.")], False)))
        assert events[-1]["type"] == "added" and not events[-1]["by_meaning"] and events[-1]["note"]
        assert service.summary(service.store.get(hr["id"]))["missing_vectors"] == 1
        done = asyncio.run(collect(service.reindex(hr["id"])))
        assert done[-1]["missing_vectors"] == 0

        rules, passages, event = asyncio.run(service.context(hr["id"], [{"role": "user", "content": "zzzz qqqq"}]))
        assert event["items"] == [] or "not" in passages

        doc = next(d for d in service.store.get(hr["id"]).documents() if d["name"] == "leave.txt")
        assert service.remove(hr["id"], doc["id"])
        hits, _ = asyncio.run(service.search(hr["id"], "sick leave doctor"))
        assert all(h.document != "leave.txt" for h in hits)
        service.delete(hr["id"])
        assert service.list() == []

        asyncio.run(chat_files(Path(tmp) / "chat"))

    print("Workspaces: splitting, adding, search by meaning and words, words alone, re-index, removal "
          "and chat files (whole, passages, reading in parts, expiry) all pass.")


if __name__ == "__main__":
    main()
