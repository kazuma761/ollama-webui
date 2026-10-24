"""Agent skills for the chat's local model - charts and memory - and the switches for them.

Modelled on AnythingLLM's agent skills (`rechart`, `memory`) and its Admin ->
Agent Skills page. The SQL connector (`sql/`) is a skill too; its switch is
here, its tools are there. Nothing in this package talks to a model.
"""

from .charts import ChartError, ChartTool, build as build_chart
from .memory import GENERAL, MemoryError_, MemoryStore, MemoryTools
from .settings import DEFAULTS, SKILLS, SkillSettings
from .toolbox import Toolbox, wants_chart, wants_memory

__all__ = [
    "DEFAULTS",
    "GENERAL",
    "SKILLS",
    "ChartError",
    "ChartTool",
    "MemoryError_",
    "MemoryStore",
    "MemoryTools",
    "SkillSettings",
    "Toolbox",
    "build_chart",
    "wants_chart",
    "wants_memory",
]
