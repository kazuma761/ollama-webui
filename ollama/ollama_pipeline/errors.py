class PipelineError(Exception):
    """Any failure the caller should show to the user as-is."""


class ModelNotFound(PipelineError):
    """The requested model id is not configured and not discoverable."""


class OllamaError(PipelineError):
    """Ollama answered with an error."""


class OllamaUnavailable(OllamaError):
    """Ollama could not be reached at all."""
