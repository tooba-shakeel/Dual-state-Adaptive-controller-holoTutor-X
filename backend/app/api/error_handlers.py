"""
Global exception handlers — map domain exceptions to HTTP responses.
"""

from __future__ import annotations

import logging
import traceback

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.exceptions import (
    AccountLockedError,
    AppError,
    AuthenticationError,
    ConflictError,
    DocumentNotFoundError,
    ExtractionError,
    PermissionDeniedError,
    ProcessingError,
    StorageError,
    ValidationError,
)
from app.core.models import ErrorResponse

logger = logging.getLogger(__name__)

# Maps exception type → HTTP status code
_STATUS_MAP: dict[type, int] = {
    ValidationError: 422,
    DocumentNotFoundError: 404,
    ExtractionError: 500,
    StorageError: 500,
    ProcessingError: 500,
    AuthenticationError: 401,
    PermissionDeniedError: 403,
    ConflictError: 409,
    AccountLockedError: 423,
}


def register_error_handlers(app: FastAPI) -> None:
    """Attach exception handlers to the FastAPI application."""

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        status = _STATUS_MAP.get(type(exc), 500)
        request_id = getattr(request.state, "request_id", None)

        logger.error(
            "[%s] %s: %s", request_id, type(exc).__name__, exc.message
        )

        body = ErrorResponse(
            error=exc.message,
            detail=exc.detail,
            request_id=request_id,
        )
        return JSONResponse(status_code=status, content=body.model_dump())

    @app.exception_handler(Exception)
    async def unhandled_error_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        logger.exception("[%s] Unhandled exception", request_id)

        body = ErrorResponse(
            error="Internal server error",
            detail=None,  # never leak stack traces to clients
            request_id=request_id,
        )
        return JSONResponse(status_code=500, content=body.model_dump())
