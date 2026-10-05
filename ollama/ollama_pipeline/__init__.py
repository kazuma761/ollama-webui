from .config import PipelineConfig, load_config
from .docgen import DocumentService, tidy_markdown
from .documents import UnsupportedDocument, extract_text
from .errors import ModelNotFound, OllamaError, OllamaUnavailable, PipelineError
from .opencode import OpenCodeClient, OpenCodeError
from .pipeline import chat_stream, picker_models, select_model, todays_theme
from .registry import ModelEntry, Registry
from .render import render as render_template
from .router import AUTO_ID, Router
from .slotmap import build as build_slotmap, summary as slotmap_summary
from .wordedit import edit as edit_word, from_markdown
from .wordfile import WordFile, analyze as analyze_word, fill as fill_word

__all__ = [
    "AUTO_ID",
    "DocumentService",
    "ModelEntry",
    "ModelNotFound",
    "OllamaError",
    "OllamaUnavailable",
    "OpenCodeClient",
    "OpenCodeError",
    "PipelineConfig",
    "PipelineError",
    "Registry",
    "Router",
    "UnsupportedDocument",
    "WordFile",
    "analyze_word",
    "build_slotmap",
    "chat_stream",
    "edit_word",
    "extract_text",
    "fill_word",
    "from_markdown",
    "load_config",
    "picker_models",
    "render_template",
    "select_model",
    "slotmap_summary",
    "tidy_markdown",
    "todays_theme",
]
