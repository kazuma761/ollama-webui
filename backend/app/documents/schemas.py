"""The request bodies of the documents API."""

from pydantic import BaseModel, Field


class PrepareRequest(BaseModel):
    model: str | None = None


class SlotmapRequest(BaseModel):
    name: str | None = None
    labels: dict[str, dict] | None = Field(default=None, description="Reviewed labels, one per paragraph id; left out to only rename")


class RenderRequest(BaseModel):
    values: dict


class Source(BaseModel):
    name: str
    content: str


class ProposeRequest(BaseModel):
    model: str | None = None
    instructions: str = ""
    sources: list[Source] = Field(default_factory=list)


class FillRequest(BaseModel):
    values: dict[str, str]


class EditRequest(BaseModel):
    model: str | None = None
    instructions: str
    version: int | None = Field(default=None, description="Version to change; the latest when left out")


class CreateRequest(BaseModel):
    model: str | None = None
    instructions: str
    sources: list[Source] = Field(default_factory=list)
    style_job: str | None = Field(default=None, description="An uploaded file whose letterhead and styles the new document takes")
