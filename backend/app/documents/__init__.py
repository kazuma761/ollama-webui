"""The backend half of the Word-documents page.

  routes.py    the /api/documents endpoints
  storage.py   uploads, versions and the template library on disk
  schemas.py   request bodies
  invoices.py  the /api/documents/invoices endpoints (the Invoices tab; nothing is stored)

The work on the files themselves is in the top-level `documents/` folder
(package `document_engine`).
"""

from .routes import router
from .storage import Library, Store

__all__ = ["Library", "Store", "router"]
