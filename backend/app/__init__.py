"""The backend. Start it with `cd backend && python -m app` (or ./run.sh).

The project's own packages live in sibling folders: `ollama/` (ollama_pipeline)
and `documents/` (document_engine). Adding those folders to the import path
here means the app starts straight from a checkout, with no install step for
them. An installed copy, if there is one, is found first and wins.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _folder in ("ollama", "documents"):
    _path = _ROOT / _folder
    if _path.is_dir() and str(_path) not in sys.path:
        sys.path.append(str(_path))
