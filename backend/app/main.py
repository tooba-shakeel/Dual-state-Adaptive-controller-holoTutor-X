"""
FastAPI application factory.

Creates and configures the application, registers routes,
middleware, error handlers, and lifecycle hooks.
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings

# ── Structured Logging ───────────────────────────────────────────────

class StructuredFormatter(logging.Formatter):
    """JSON-like structured log formatter for production readability."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = self.formatTime(record, self.datefmt)
        level = record.levelname
        logger_name = record.name
        message = record.getMessage()

        # Include exception info if present
        exc_text = ""
        if record.exc_info and record.exc_info[0]:
            exc_text = f" │ exception={self.formatException(record.exc_info)}"

        return f"{timestamp} │ {level:<8s} │ {logger_name} │ {message}{exc_text}"


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(StructuredFormatter(datefmt="%Y-%m-%d %H:%M:%S"))

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    handlers=[handler],
)
logger = logging.getLogger(__name__)


# ── Lifespan ─────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    from app.dependencies import startup, shutdown

    await startup()
    yield
    await shutdown()


# ── App factory ──────────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="AI-powered PDF Retrieval-Augmented Generation system",
        lifespan=lifespan,
    )

    # Middleware
    from app.api.middleware import register_middleware

    register_middleware(app)

    # Error handlers
    from app.api.error_handlers import register_error_handlers

    register_error_handlers(app)

    # Routes
    from app.api.routes.health import router as health_router
    from app.api.routes.documents import router as documents_router
    from app.api.routes.chat import router as chat_router
    from app.api.routes.export import router as export_router
    from app.api.routes import feedback
    from app.api.routes.auth import router as auth_router

    app.include_router(auth_router)

   
    app.include_router(feedback.router)
    app.include_router(health_router)
    app.include_router(documents_router)
    app.include_router(chat_router)
    app.include_router(export_router)

    # Serve frontend as static files
    frontend_dir = Path(__file__).resolve().parent.parent.parent / "frontend"
    if frontend_dir.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
        logger.info("Serving frontend from %s", frontend_dir)
    else:
        logger.warning("Frontend directory not found at %s", frontend_dir)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=True,
    )
