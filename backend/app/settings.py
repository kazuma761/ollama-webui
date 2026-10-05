import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Loaded before the pipeline config so OLLAMA_HOST in .env takes effect.
load_dotenv(BACKEND_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    reload: bool
    cors_origins: list[str]
    frontend_dir: Path
    documents_dir: Path


settings = Settings(
    host=os.environ.get("HOST", "127.0.0.1"),
    port=int(os.environ.get("PORT", "8000")),
    reload=os.environ.get("RELOAD", "false").lower() in {"1", "true", "yes"},
    cors_origins=[o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()],
    frontend_dir=Path(os.environ.get("FRONTEND_DIR") or BACKEND_DIR.parent / "frontend"),
    documents_dir=Path(os.environ.get("DOCUMENTS_DIR") or BACKEND_DIR / "data" / "documents"),
)
