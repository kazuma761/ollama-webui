from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatFile(BaseModel):
    name: str
    content: str


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = ""
    images: list[str] = Field(default_factory=list, description="Base64-encoded images, no data: prefix")
    files: list[ChatFile] = Field(default_factory=list, description="Text files folded into the prompt")
    model: str | None = Field(default=None, description="On assistant messages: the model id that wrote it")


class ChatRequest(BaseModel):
    model: str | None = Field(default=None, description="Model id from /api/models; \"auto\" routes per message; omit for the default")
    messages: list[ChatMessage] = Field(min_length=1)
    options: dict[str, Any] | None = Field(default=None, description="Ollama options, e.g. temperature")
