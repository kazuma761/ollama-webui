import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .settings import settings  # isort: skip - loads .env before the pipeline reads the environment
from document_engine import DocumentService
from document_engine.invoices import InvoiceService
from document_engine.word import WordService
from ollama_pipeline import Registry, Router, load_config
from sql_connector import ConnectionStore
from workspace_engine import ChatFiles, Embedder, WorkspaceService
from agent_skills import MemoryStore, SkillSettings

from .documents import Library, Store, router as documents_router
from .documents.invoices import router as invoices_router
from .documents.word import router as word_router
from .admin import public as skills_router, router as admin_router
from .routes import router
from .sql import router as sql_router
from .workspaces import router as workspaces_router

# uvicorn only sets up its own loggers; without this the pipeline's INFO lines
# (e.g. "Router: Von is ready") never reach the server log.
logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(message)s")
for noisy in ("httpx", "httpcore"):
    logging.getLogger(noisy).setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    app.state.registry = Registry(config)
    app.state.registry.warm_up()
    app.state.router = Router(config.router)
    app.state.router.warm_up()
    app.state.documents = DocumentService(app.state.registry)
    app.state.invoices = InvoiceService(app.state.registry)
    app.state.word = WordService(app.state.registry)
    app.state.store = Store(settings.documents_dir, config.documents.keep_hours)
    app.state.library = Library(settings.documents_dir.parent / "templates")
    app.state.sql_store = ConnectionStore(settings.sql_dir)
    embedding = Embedder(app.state.registry)  # one, so workspaces and chat files share its state
    app.state.workspaces = WorkspaceService(app.state.registry, settings.workspaces_dir, embedding)
    app.state.chatfiles = ChatFiles(app.state.registry, settings.chatfiles_dir, embedding, config.documents.keep_hours)
    app.state.skills = SkillSettings(settings.admin_dir)
    app.state.memory = MemoryStore(settings.admin_dir)
    yield
    await app.state.registry.aclose()


def create_app() -> FastAPI:
    app = FastAPI(title="Fastshot API", version="0.1.0", lifespan=lifespan)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["Content-Type", "X-Admin-Password"],
        )

    app.include_router(router, prefix="/api")
    app.include_router(invoices_router, prefix="/api/documents/invoices")  # before the /{job} routes below
    app.include_router(word_router, prefix="/api/documents/word")  # the same
    app.include_router(documents_router, prefix="/api/documents")
    app.include_router(sql_router, prefix="/api/sql")
    app.include_router(workspaces_router, prefix="/api/workspaces")
    app.include_router(admin_router, prefix="/api/admin")
    app.include_router(skills_router, prefix="/api/skills")

    # Mounted last so /api and /docs win over the static catch-all.
    if settings.frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")

    return app


app = create_app()
