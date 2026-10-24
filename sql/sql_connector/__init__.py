"""The SQL connector: saved database connections, and read-only tools a local model calls in the chat.

Rebuilt from AnythingLLM's SQL agent skill
(server/utils/agents/aibitat/plugins/sql-agent/), in Python. Nothing in this
package talks to a model; the chat pipeline runs the tool calls it gets back.
"""

from .connectors import ENGINES, Connection, DatabaseError, check_fields, connector, missing_driver
from .safety import UnsafeQuery, check
from .sheets import SheetError, as_markdown, describe, import_file, is_spreadsheet
from .store import ConnectionStore, slug
from .tools import SqlTools

__all__ = [
    "ENGINES",
    "Connection",
    "ConnectionStore",
    "DatabaseError",
    "SheetError",
    "SqlTools",
    "UnsafeQuery",
    "as_markdown",
    "check",
    "check_fields",
    "connector",
    "describe",
    "import_file",
    "is_spreadsheet",
    "missing_driver",
    "slug",
]
