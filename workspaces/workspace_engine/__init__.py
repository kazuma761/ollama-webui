"""Workspaces: documents a team adds once, split into passages, indexed on this
machine, and searched for every question asked in the workspace (RAG).

Modelled on AnythingLLM's workspaces. The index is one SQLite file per
workspace: full-text search (FTS5) for words, and vectors from a local
embedding model for meaning. Nothing leaves the machine.
"""

from .chatfiles import ChatFile, ChatFiles, wants_whole
from .chunking import Passage, passages, split
from .embedding import Embedder
from .index import Hit, Workspace, WorkspaceError, Workspaces
from .service import WorkspaceService

__all__ = ["ChatFile", "ChatFiles", "Embedder", "Hit", "Passage", "Workspace", "WorkspaceError", "WorkspaceService", "Workspaces", "passages", "split", "wants_whole"]
