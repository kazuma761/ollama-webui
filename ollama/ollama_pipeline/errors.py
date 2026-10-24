class PipelineError(Exception):
    """Any failure the caller should show to the user as-is."""


class ModelNotFound(PipelineError):
    """The requested model id is not configured and not discoverable."""


class OllamaError(PipelineError):
    """Ollama answered with an error."""


class OllamaUnavailable(OllamaError):
    """Ollama could not be reached at all."""


class ContextOverflow(OllamaError):
    """The server refused a request that does not fit in its context.

    `prompt_tokens` and `context` are the server's own counts, when it gave them.
    """

    def __init__(self, message: str, prompt_tokens: int | None = None, context: int | None = None):
        super().__init__(message)
        self.prompt_tokens = prompt_tokens
        self.context = context
