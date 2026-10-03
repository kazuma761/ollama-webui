"""Auto-routing: decides which model should answer each message.

The decision comes from Von (a small non-generative decision model) when it
is installed and confident. In every other case - Von missing, still
loading, erroring, slow or unsure - plain keyword rules decide instead, so
routing can never be the reason a chat fails.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
from dataclasses import dataclass
from typing import Any

from .config import RouterConfig

log = logging.getLogger(__name__)

AUTO_ID = "auto"

MAX_STATE_CHARS = 4000
DECIDE_TIMEOUT_SECONDS = 8.0

# Signals that a request needs the stronger model. Deliberately broad: sending
# an easy request to the heavy model costs time, the reverse costs a bad answer.
_HARD = re.compile(
    r"```|\b("
    r"code|coding|function|class|method|bug|debug|error|exception|traceback|stack trace|compile|"
    r"algorithm|complexity|refactor|optimi[sz]e|regex|sql|query|schema|api|endpoint|script|"
    r"python|javascript|typescript|java|rust|golang|c\+\+|c#|bash|docker|kubernetes|"
    r"prove|proof|derive|calculate|equation|probability|logic|logical|reasoning|"
    r"architecture|design a|trade-?offs?|step[- ]by[- ]step|analy[sz]e|evaluate|compare|"
    r"root cause|why does|why is|explain why"
    r")\b",
    re.IGNORECASE,
)
_LONG_REQUEST_CHARS = 800
# A message this short with no hard signal ("make it shorter", "and in French?") is
# treated as a follow-up to whatever the current model was already doing.
_FOLLOW_UP_WORDS = 8


@dataclass(frozen=True)
class Decision:
    model: str  # id of the model entry to use
    route: str | None  # route name, None for the image rule
    source: str  # "von" | "rules" | "sticky" | "image"
    confidence: float | None = None

    def event(self) -> dict[str, Any]:
        return {"route": self.route, "source": self.source, "confidence": self.confidence}


def _last_user(messages: list[dict[str, Any]]) -> dict[str, Any]:
    return next((m for m in reversed(messages) if m["role"] == "user"), {})


def _previous_model(messages: list[dict[str, Any]]) -> str | None:
    """The model that wrote the latest reply, as recorded by the frontend."""
    return next((m.get("model") for m in reversed(messages) if m["role"] == "assistant" and m.get("model")), None)


def _state(message: dict[str, Any]) -> str:
    """What the router reads: the request itself, plus a note about attachments."""
    text = (message.get("content") or "").strip()[:MAX_STATE_CHARS]
    files = [f["name"] for f in message.get("files") or []]
    if files:
        text += f"\n\n[The user attached {len(files)} document(s) to read: {', '.join(files)}]"
    return text


class Router:
    def __init__(self, config: RouterConfig):
        self.config = config
        self._von: Any = None
        self._von_failed = False
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def models(self) -> list[str]:
        """Every model id routing can pick, strongest first, without duplicates."""
        config = self.config
        ordered = [config.route(config.heavy_route), *config.routes]
        ids = [r.model for r in ordered if r] + ([config.image_model] if config.image_model else [])
        return list(dict.fromkeys(ids))

    # ── Von ──

    def _load_von(self) -> None:
        with self._lock:
            if self._von is not None or self._von_failed:
                return
            try:
                os.environ.setdefault("VON_DEVICE", self.config.device)
                import von

                # One throwaway decision so the weights are downloaded and loaded now.
                von.decide(state="hello", choices={"a": "greeting", "b": "question"}, instructions="Classify.")
                self._von = von
                log.info("Router: Von is ready (device=%s)", os.environ["VON_DEVICE"])
            except ImportError:
                self._von_failed = True
                log.warning("Router: von-sdk is not installed, using keyword rules. Install it with: uv sync --extra router")
            except Exception:
                self._von_failed = True
                log.exception("Router: Von failed to load, using keyword rules")

    def warm_up(self) -> None:
        """Loads Von in the background; until it is ready, rules decide."""
        if self.enabled and self.config.backend == "von":
            threading.Thread(target=self._load_von, name="router-warmup", daemon=True).start()

    def _ask_von(self, state: str) -> tuple[str, float]:
        result = self._von.decide(
            state=state,
            choices={r.name: r.description for r in self.config.routes},
            instructions=self.config.instructions,
        )
        return result.choice, float(result.confidence)

    # ── Rules ──

    def _is_hard(self, message: dict[str, Any]) -> bool:
        text = message.get("content") or ""
        return bool(_HARD.search(text)) or len(text) > _LONG_REQUEST_CHARS or bool(message.get("files"))

    def _is_follow_up(self, message: dict[str, Any]) -> bool:
        return not self._is_hard(message) and len((message.get("content") or "").split()) <= _FOLLOW_UP_WORDS

    # ── Decision ──

    async def decide(self, messages: list[dict[str, Any]]) -> Decision:
        config = self.config
        message = _last_user(messages)

        if message.get("images") and config.image_model:
            return Decision(config.image_model, None, "image")

        previous = _previous_model(messages)
        current = next((r for r in config.routes if r.model == previous), None)

        # A short follow-up like "make it shorter" stays with the model already in use. This is
        # checked before Von: it only reads the latest message, and on its own such a message
        # looks like an easy request no matter how hard the task it refers to is.
        if current and self._is_follow_up(message):
            return Decision(current.model, current.name, "sticky")

        confidence = None
        if self._von is not None:
            try:
                name, confidence = await asyncio.wait_for(
                    asyncio.to_thread(self._ask_von, _state(message)), DECIDE_TIMEOUT_SECONDS
                )
                route = config.route(name)
                if route and confidence >= config.min_confidence:
                    return Decision(route.model, route.name, "von", round(confidence, 3))
            except Exception:
                log.exception("Router: Von failed on this request, using keyword rules")

        confidence = round(confidence, 3) if confidence is not None else None

        # Von looked and was unsure: stay with the model already in use.
        if current and confidence is not None:
            return Decision(current.model, current.name, "sticky", confidence)

        route = config.route(config.heavy_route if self._is_hard(message) else config.default_route)
        return Decision(route.model, route.name, "rules", confidence)
