"""Auto-routing: decides which model should answer each message.

The decision comes from Von (a small non-generative decision model) when it
is installed and confident. In every other case - Von missing, still
loading, erroring, slow or unsure - plain keyword rules decide instead, so
routing can never be the reason a chat fails.

Requests about software can have routes of their own whose models run in the
cloud. Those are taken on clear keywords or a confident Von, and never by a
conversation that has had an attachment.
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
    r"prove|proof|derive|solve|calculate|equation|probability|logic|logical|reasoning|"
    r"architecture|design a|trade-?offs?|step[- ]by[- ]step|analy[sz]e|evaluate|compare|"
    r"root cause|why does|why is|explain why"
    r")\b",
    re.IGNORECASE,
)
# Signals that a request is about software, for the cloud routes. Deliberately narrow, the
# opposite of _HARD: a coding question that is missed only gets a local answer, but an everyday
# message that matches leaves this machine. So nothing that is also an everyday word ("class",
# "error", "script", "java", "react"); Von picks up what these miss.
_SOFTWARE = re.compile(
    r"```|`[^`\n]+`|==|!=|&&|\|\||\+=|\w\s*=\s*[\[{]|\b\w+\.\w+\(|\b(print|printf|println|len|range)\(|\bc\+\+|\bc#|"
    # pasted commands: `$name = ...`, `2>&1`, ` --flag`, file names such as app.py or tool.exe
    r"\$\w+\s*=|2>&1|\s--[a-z][\w-]+|\b[\w-]+\.(py|js|ts|jsx|tsx|json|ya?ml|html|css|sh|ps1|exe|sql|cpp|java|go|rs)\b|"
    r"\b("
    r"(write|give|create|make|generate|show|need|want)\w* (me )?(a |an |the |some )?code|"
    r"(write|create|make|build)\w* (me )?(a |an )?(\w+ )?program (to|that|which)|node_modules|"
    r"python|javascript|typescript|golang|kotlin|php|sql|mysql|postgres\w*|sqlite|mongodb|redis|"
    r"html|css|json|yaml|xml|regex|powershell|linux|(bash|shell) script|"
    r"git|github|gitlab|docker\w*|kubernetes|k8s|npm|nginx|node\.?js|django|fastapi|numpy|pytorch|tensorflow|"
    r"source code|codebase|coding|programming|programmer|apis?|endpoint|backend|frontend|"
    r"(write|fix|debug|refactor|review|optimi[sz]e|explain|run|test)\w* (the |this |my |some |that |your )?code|"
    r"writ\w+ (a |an |the |me a )?(\w+ )?function|(fix|find|found|debug)\w* (the |this |my |a |that )?bug|"
    r"stack ?trace|traceback|syntax error|runtime error|compiler|compile error|compilation|null pointer|"
    r"segfault|segmentation fault|unit tests?|pull request|merge conflict|graphql|dockerfile|localhost|"
    r"algorithm|recursion|binary search|time complexity|data structures?|linked list|hash ?(map|table)|"
    r"(for|while|infinite) loop|database (schema|query|table|index|migration)|debug\w*|refactor\w*|"
    r"microservices?|system design|software (architecture|design|engineering)|"
    r"design (a|an|the|our|my) [\w\s-]{0,40}(system|service|platform|pipeline|backend|schema|api)|"
    r"race conditions?|deadlock\w*|memory leaks?|concurrency|thread[- ]safe\w*|heisenbug|"
    r"load balanc\w+|message queue|event[- ]driven"
    r")\b",
    re.IGNORECASE,
)
# Among requests about software, the ones for the expert route: system design and the kinds
# of bug that are hard to find.
_EXPERT = re.compile(
    r"\b("
    r"architecture|architect|system design|design (a|an|the|our|my) [\w\s-]{0,40}(system|service|platform|pipeline|backend|schema|api)|"
    r"microservices?|distributed|scalab\w+|high availability|load balanc\w+|event[- ]driven|message queue|"
    r"race conditions?|deadlock\w*|memory leaks?|concurrency|thread[- ]safe\w*|segfault|segmentation fault|"
    r"bottlenecks?|intermittent\w*|flaky|heisenbug|hard to (debug|reproduce)|"
    r"refactor\w*|migrat\w+|design patterns?|trade-?offs?"
    r")\b",
    re.IGNORECASE,
)
# What Von is asked when the keywords above find nothing: is this request about software?
# Two questions, and a request goes to the cloud only if it passes both. Tried on 35 sample
# requests, the first alone also says yes to "hey" and to an email about a website, and the
# second alone to a maths problem; together they caught 15 of 16 software requests and let
# through 2 of 19 others (both asked for a "script" - for a film and for a school event).
_SOFTWARE_QUESTION = "Is this request about software?"
_SOFTWARE_YES = (
    "Software, code or websites: writing, fixing, explaining or reviewing code, scripts, commands, "
    "queries or configuration; building an app, a website or a web page; error messages, "
    "developer tools, APIs, databases, system design."
)
_SOFTWARE_NO = (
    "Anything else: emails, messages and letters, translation, summaries, bills and invoices, "
    "contracts and documents, maths and logic problems, general knowledge, advice, small talk."
)
_SOFTWARE_MIN = 0.85
_SOFTWARE_CONFIRM = (
    "Is the user asking for help with code, programming, building a website or app, "
    "or a technical computer problem?"
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
        """Every model id routing can pick, in fallback order: local heavy, local light, the rest, cloud last."""
        config = self.config
        cloud = self._cloud()
        local = [r for r in config.routes if r.name not in cloud]
        ordered = [config.route(config.heavy_route), config.route(config.default_route), *local]
        ids = [m for r in ordered if r for m in r.models] + ([config.image_model] if config.image_model else [])
        ids += [m for r in config.routes if r.name in cloud for m in r.models]
        return list(dict.fromkeys(ids))

    def _cloud(self) -> set[str]:
        """Names of the routes whose models are not on this machine."""
        return {self.config.code_route, self.config.expert_route} - {""}

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
        # Von only chooses between the local routes. Every route added to the choice makes it
        # less sure of all of them, and it cannot tell the cloud routes from "complex";
        # `_von_says_software` asks about those separately.
        local = [r for r in self.config.routes if r.name not in self._cloud()]
        result = self._von.decide(
            state=state,
            choices={r.name: r.description for r in local},
            instructions=self.config.instructions,
        )
        return result.choice, float(result.confidence)

    def _von_says_software(self, state: str) -> float | None:
        """How sure Von is that the request is about software, or None when it is not."""
        first = self._von.decide(
            state=state, choices={"software": _SOFTWARE_YES, "other": _SOFTWARE_NO}, instructions=_SOFTWARE_QUESTION
        )
        sure = float(first.probabilities["software"])
        if sure < _SOFTWARE_MIN or self._von.judge(state=state, instructions=_SOFTWARE_CONFIRM) < 0.5:
            return None
        return sure

    # ── Rules ──

    def _is_hard(self, message: dict[str, Any]) -> bool:
        text = message.get("content") or ""
        return bool(_HARD.search(text)) or len(text) > _LONG_REQUEST_CHARS or bool(message.get("files"))

    def _is_follow_up(self, message: dict[str, Any]) -> bool:
        return not self._is_hard(message) and len((message.get("content") or "").split()) <= _FOLLOW_UP_WORDS

    def _software_route(self, message: dict[str, Any]) -> str:
        """The cloud route for a request about software, or "" when there is none for it.

        The expert route takes system design and hard-to-find bugs, the code route the rest.
        """
        config = self.config
        if config.expert_route and _EXPERT.search(message.get("content") or ""):
            return config.expert_route
        return config.code_route

    def _by_rules(self, message: dict[str, Any]) -> str:
        config = self.config
        if _SOFTWARE.search(message.get("content") or "") and self._software_route(message):
            return self._software_route(message)
        return config.heavy_route if self._is_hard(message) else config.default_route

    def _keep_local(self, route_name: str, messages: list[dict[str, Any]]) -> str:
        """A conversation that has had any attachment never goes to a cloud route.

        That covers the documents themselves and what local models said about them.
        """
        private = any(m.get("files") or m.get("images") for m in messages)
        if route_name in self._cloud() and private:
            return self.config.heavy_route
        return route_name

    # ── Decision ──

    async def decide(self, messages: list[dict[str, Any]]) -> Decision:
        decision = await self._decide(messages)
        # The reason a message went where it did, without the message itself.
        log.info(
            "Router: %s -> %s (%s%s)",
            decision.route or "image",
            decision.model,
            decision.source,
            "" if decision.confidence is None else f", {decision.confidence}",
        )
        return decision

    async def _decide(self, messages: list[dict[str, Any]]) -> Decision:
        config = self.config
        message = _last_user(messages)

        if message.get("images") and config.image_model:
            return Decision(config.image_model, None, "image")

        # The model that answered last, and its route - unless that is a cloud route and the
        # chat now has attachments in it.
        previous = _previous_model(messages)
        current = next((r for r in config.routes if r.model == previous), None) or next(
            (r for r in config.routes if previous in r.models), None
        )
        if current and self._keep_local(current.name, messages) != current.name:
            current = None

        # A short follow-up like "make it shorter" stays with the model already in use. This is
        # checked before Von: it only reads the latest message, and on its own such a message
        # looks like an easy request no matter how hard the task it refers to is.
        if current and self._is_follow_up(message):
            return Decision(previous, current.name, "sticky")

        # Clear signs of a software question send it to the cloud without asking Von.
        by_rules = self._keep_local(self._by_rules(message), messages)
        if by_rules in self._cloud():
            return Decision(config.route(by_rules).model, by_rules, "rules")

        confidence = None
        if self._von is not None:
            try:
                state = _state(message)
                # Software the keywords did not catch ("make a landing page for my shop"): the
                # cloud, unless the chat has attachments or there is no cloud route for it.
                cloud = self._software_route(message)
                if cloud and self._keep_local(cloud, messages) == cloud:
                    sure = await asyncio.wait_for(
                        asyncio.to_thread(self._von_says_software, state), DECIDE_TIMEOUT_SECONDS
                    )
                    if sure is not None:
                        return Decision(config.route(cloud).model, cloud, "von", round(sure, 3))
                name, confidence = await asyncio.wait_for(
                    asyncio.to_thread(self._ask_von, state), DECIDE_TIMEOUT_SECONDS
                )
                route = config.route(name)
                if route and confidence >= config.min_confidence:
                    return Decision(route.model, route.name, "von", round(confidence, 3))
            except Exception:
                log.exception("Router: Von failed on this request, using keyword rules")

        confidence = round(confidence, 3) if confidence is not None else None

        # Von looked and was unsure: stay with the model already in use, if it is a local one.
        # A chat does not stay in the cloud on a message that is not about software.
        if current and confidence is not None and current.name not in self._cloud():
            return Decision(previous, current.name, "sticky", confidence)

        route = config.route(by_rules)
        return Decision(route.model, route.name, "rules", confidence)
