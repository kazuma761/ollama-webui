from .config import PipelineConfig, load_config
from .documents import UnsupportedDocument, extract_text
from .errors import ModelNotFound, OllamaError, OllamaUnavailable, PipelineError
from .opencode import OpenCodeClient, OpenCodeError
from .pipeline import chat_stream, picker_models, select_model, todays_theme
from .registry import ModelEntry, Registry
from .router import AUTO_ID, Router

__all__ = [
    "AUTO_ID",
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
    "chat_stream",
    "extract_text",
    "load_config",
    "picker_models",
    "select_model",
    "todays_theme",
]
