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
    sql_dir: Path
    workspaces_dir: Path
    admin_dir: Path
    chatfiles_dir: Path
    admin_password: str  # empty = the Admin panel is closed


settings = Settings(
    host=os.environ.get("HOST", "127.0.0.1"),
    port=int(os.environ.get("PORT", "8000")),
    reload=os.environ.get("RELOAD", "false").lower() in {"1", "true", "yes"},
    cors_origins=[o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()],
    frontend_dir=Path(os.environ.get("FRONTEND_DIR") or BACKEND_DIR.parent / "frontend"),
    documents_dir=Path(os.environ.get("DOCUMENTS_DIR") or BACKEND_DIR / "data" / "documents"),
    sql_dir=Path(os.environ.get("SQL_DIR") or BACKEND_DIR / "data" / "sql"),
    workspaces_dir=Path(os.environ.get("WORKSPACES_DIR") or BACKEND_DIR / "data" / "workspaces"),
    admin_dir=Path(os.environ.get("ADMIN_DIR") or BACKEND_DIR / "data" / "admin"),
    chatfiles_dir=Path(os.environ.get("CHATFILES_DIR") or BACKEND_DIR / "data" / "chatfiles"),
    admin_password=os.environ.get("ADMIN_PASSWORD", ""),
)
