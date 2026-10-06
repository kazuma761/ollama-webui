"""The Word-documents feature: everything behind frontend/documents.html except the HTTP routes.

  engine/   reads and changes Word files (python-docx). No model.
              wordfile.py  open a file, number its paragraphs, find blanks, fill them
              wordedit.py  the fixed list of edit operations; Markdown -> Word
              slotmap.py   labels of a designed template -> its slot map
              render.py    fill a designed template, copying or removing repeated blocks
  model/    the model's part. It decides; it never writes a file.
              prompts.py   every instruction the model gets, and the JSON it must answer in
              service.py   sends those to the local model and checks what comes back

It builds on the `ollama_pipeline` package for the model registry, the Ollama
client and the settings (`documents:` in ollama/config/models.yaml).
"""

from .engine.render import render as render_template
from .engine.slotmap import build as build_slotmap, summary as slotmap_summary
from .engine.wordedit import edit as edit_word, from_markdown
from .engine.wordfile import WordFile, analyze as analyze_word, fill as fill_word
from .model.service import DocumentService, tidy_markdown

__all__ = [
    "DocumentService", "WordFile", "analyze_word", "build_slotmap", "edit_word", "fill_word",
    "from_markdown", "render_template", "slotmap_summary", "tidy_markdown",
]
