"""How much of a conversation fits in one request to the model, and making it fit.

llama-server gives each request the context of one slot: `-c` divided by
`-np`, or all of `-c` with `--kv-unified`. A prompt larger than that is
refused ("the request exceeds the available context size"). Ollama cuts the
start of it without saying so. Either way the model loses something: the
beginning of a long chat, or most of a big file.

So every request is fitted here before it is sent, the way AnythingLLM's
`messageArrayCompressor` does it, with the least important things going first:

1. older turns of the conversation, oldest first;
2. if the instructions, the question and this turn's tool results alone are
   still too big, the largest of them is shortened in the middle, keeping its
   beginning and its end (where the question is).

Room is always kept for the answer. Big files do not depend on this: the
backend sends only the passages of a file that fit (see workspace_engine's
ChatFiles); this is the last line of defence.

Tokens are estimated, not counted: counting would mean sending every text to
the server first. The estimate leans high, and when the server still refuses
a request its error says by how much, so `chat_stream` retries once with a
budget scaled by the real numbers.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any

PER_MESSAGE = 8  # the chat template's own tokens around each message
PER_IMAGE = 800  # a picture, as a vision model reads it (Gemma: 256; others more)
MIN_ANSWER = 1024  # tokens always kept free for the answer
MAX_ANSWER = 4096


def estimate(text: str) -> int:
    """Tokens in `text`, estimated on the high side.

    English is about 4 characters a token, numbers about 2 (tokenizers split
    them into short groups), and Indian scripts nearer 1 to 2.
    """
    if not text:
        return 0
    ascii_chars = len(text.encode("ascii", "ignore"))
    digits = sum(text.count(d) for d in "0123456789")
    other = len(text) - ascii_chars
    return int(((ascii_chars - digits) / 3.8 + digits / 2.2 + other / 1.5) * 1.1) + 1


def message_tokens(message: dict[str, Any]) -> int:
    content = message.get("content") or ""
    if not isinstance(content, str):
        content = json.dumps(content)
    tokens = estimate(content) + PER_MESSAGE + PER_IMAGE * len(message.get("images") or [])
    if message.get("tool_calls"):
        tokens += estimate(json.dumps(message["tool_calls"], default=str))
    return tokens


def total_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(message_tokens(m) for m in messages)


def shrink(budget: Budget | None, sent: list[dict[str, Any]], prompt_tokens: int | None, context: int | None) -> Budget:
    """A smaller budget after the server refused `sent`, using its own counts when it gave them."""
    if prompt_tokens and context:
        off_by = max(1.0, prompt_tokens / max(1, total_tokens(sent)))  # how far the estimate fell short
        reserve = min(budget.reserve, context // 3) if budget else min(MAX_ANSWER, max(MIN_ANSWER, context // 5))
        prompt = int((context - reserve) / off_by * 0.92)
        return Budget(context, max(reserve, context - prompt))
    if budget:
        return budget.scaled(0.7)
    return Budget(4096, MIN_ANSWER)


@dataclass(frozen=True)
class Budget:
    """What one request may hold: `context` tokens in all, `reserve` of them kept for the answer."""

    context: int
    reserve: int

    @property
    def prompt(self) -> int:
        return max(256, self.context - self.reserve)

    def scaled(self, factor: float) -> Budget:
        return Budget(self.context, max(MIN_ANSWER // 2, self.context - int(self.prompt * factor)))


def budget_for(context: int | None, options: dict[str, Any] | None = None) -> Budget | None:
    """The budget of a model whose requests hold `context` tokens; None when that is unknown."""
    if not context:
        return None
    reserve = min(MAX_ANSWER, max(MIN_ANSWER, context // 5))
    wanted = int((options or {}).get("num_predict") or 0)
    if wanted > 0:
        reserve = max(256, min(wanted, context // 2))
    return Budget(int(context), min(reserve, int(context) // 2))


def cut_middle(text: str, tokens: int) -> str:
    """`text` shortened to about `tokens` by taking out its middle, keeping the start and the end."""
    have = estimate(text)
    if have <= tokens:
        return text
    keep = max(0, int(len(text) * tokens / have) - 80)
    head, tail = text[: int(keep * 0.6)], text[len(text) - int(keep * 0.4):] if keep else ""
    gone = len(text) - len(head) - len(tail)
    return f"{head}\n\n[... {gone:,} characters left out here to fit the model's context ...]\n\n{tail}"


@dataclass
class Fitted:
    messages: list[dict[str, Any]]
    dropped: int = 0  # older messages left out
    shortened: int = 0  # messages cut in the middle

    def notice(self, budget: Budget) -> str | None:
        if not self.dropped and not self.shortened:
            return None
        parts = []
        if self.dropped:
            parts.append(f"the {self.dropped} oldest message{'s' if self.dropped != 1 else ''} of this chat")
        if self.shortened:
            parts.append(f"part of {self.shortened} long message{'s' if self.shortened != 1 else ''}")
        return (f"To fit what the model can read at once ({budget.context:,} tokens), "
                f"{' and '.join(parts)} {'was' if len(parts) == 1 and self.dropped + self.shortened == 1 else 'were'} left out.")


def fit(messages: list[dict[str, Any]], limit: int, keep_from: int | None = None) -> Fitted:
    """The messages, made to hold at most `limit` tokens. The input is not changed.

    `keep_from` is where the current turn starts (its question); everything from
    there on is kept, shortened if it must be. By default: the last user message.
    """
    if total_tokens(messages) <= limit:
        return Fitted(messages)

    first = 0
    while first < len(messages) and messages[first]["role"] == "system":
        first += 1
    last_user = keep_from if keep_from is not None else max(
        (i for i, m in enumerate(messages) if m["role"] == "user"), default=len(messages) - 1)
    last_user = min(max(last_user, first), len(messages))
    head = [dict(m) for m in messages[:first]]
    history = messages[first:last_user]
    current = [dict(m) for m in messages[last_user:]]
    result = Fitted([])

    # This turn's tool results, except the newest, were read already: they can be short.
    tools = [m for m in current[:-1] if m["role"] == "tool"]
    for m in tools:
        if message_tokens(m) > 600 and total_tokens(head + current) > limit:
            m["content"] = cut_middle(m["content"], 500)
            result.shortened += 1

    # The instructions, the question and this turn's work come first. If they alone are
    # too big, the largest of them gives way, a little at a time.
    for _ in range(12):
        over = total_tokens(head + current) - limit
        if over <= 0:
            break
        largest = max(head + current, key=message_tokens)
        target = max(300, message_tokens(largest) - over - 64)
        if target >= message_tokens(largest):
            break
        largest["content"] = cut_middle(largest.get("content") or "", target)
        result.shortened += 1

    # Then as much of the conversation as fits, newest first.
    room = limit - total_tokens(head + current)
    kept: list[dict[str, Any]] = []
    for message in reversed(history):
        tokens = message_tokens(message)
        if tokens <= room:
            kept.insert(0, message)
            room -= tokens
            continue
        if len(kept) < 2 and room > 400:  # the turn just before this one matters most: keep part of it
            short = copy.copy(message)
            short["content"] = cut_middle(message.get("content") or "", room - PER_MESSAGE - 32)
            kept.insert(0, short)
            result.shortened += 1
        break
    # Chat templates (Gemma's among them) want the conversation to start with the user.
    while kept and kept[0]["role"] != "user":
        kept.pop(0)
    result.dropped = len(history) - len(kept)
    result.messages = head + kept + current
    return result

