import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .settings import settings  # isort: skip - loads .env before the pipeline reads the environment
from ollama_pipeline import DocumentService, Registry, Router, load_config

from .documents import Library, Store, router as documents_router
from .routes import router

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
    app.state.store = Store(settings.documents_dir, config.documents.keep_hours)
    app.state.library = Library(settings.documents_dir.parent / "templates")
    yield
    await app.state.registry.aclose()


def create_app() -> FastAPI:
    app = FastAPI(title="Fastshot API", version="0.1.0", lifespan=lifespan)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
        )

    app.include_router(router, prefix="/api")
    app.include_router(documents_router, prefix="/api/documents")

    # Mounted last so /api and /docs win over the static catch-all.
    if settings.frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")

    return app


app = create_app()
