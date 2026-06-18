"""FastAPI application factory + global error handling + lifespan."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.logging_config import configure_logging
from app.middleware import RequestContextMiddleware
from app.routers import chat, conversations, health, sources
from database import db

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)

    db.init_pool(
        database_url=settings.database_url,
        minconn=settings.db_pool_min,
        maxconn=settings.db_pool_max,
    )
    logger.info("%s v%s started (env=%s)",
                settings.app_name, settings.app_version, settings.environment)
    try:
        yield
    finally:
        db.close_pool()
        logger.info("%s shutdown complete", settings.app_name)


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Agentic RAG over a curated vector store of legal judgements.",
        docs_url="/docs" if settings.environment != "production" else "/docs",
        redoc_url=None,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestContextMiddleware)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": exc.errors()},
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", None)
        logger.exception("unhandled error rid=%s path=%s", request_id, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "internal server error", "request_id": request_id},
        )

    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(conversations.router)
    app.include_router(sources.router)

    @app.get("/api", tags=["meta"], include_in_schema=False)
    async def api_meta():
        return {"service": settings.app_name, "version": settings.app_version, "docs": "/docs"}

    # --- Frontend (minimal single-page UI) ------------------------------------
    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        @app.get("/", include_in_schema=False)
        async def index():
            return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
