"""Invoices into a sheet: reads invoices (PDF, Word, photos) into fixed columns.

Separate from the Word-documents code in `engine/` and `model/`; it only shares
the model registry and the Ollama client with them.

  reader.py    a file -> its pages, each as text and/or a picture       no model
  checks.py    cleans the values, checks them, scores the confidence    no model
  export.py    the reviewed rows -> .xlsx or .csv                       no model
  prompts.py   what the model is told and the JSON it must answer in    the model's part
  service.py   sends each page to the local model, merges the answers   the model's part

Try it from a terminal:  python -m document_engine.invoices <files or a folder>
"""

from .checks import COLUMNS
from .export import to_csv, to_xlsx
from .reader import ACCEPTED, read_file
from .service import InvoiceService

__all__ = ["ACCEPTED", "COLUMNS", "InvoiceService", "read_file", "to_csv", "to_xlsx"]
