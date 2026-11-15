"""
Document service — high-level operations for uploading, listing,
inspecting, and deleting documents.

This is the primary entry-point called by API routes.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import uuid
import hashlib
from pathlib import Path
from typing import Any

from app.config import settings
from app.core.exceptions import DocumentNotFoundError, ValidationError
from app.core.interfaces import MetadataStore, VectorStore
from app.core.models import (
    Document,
    DocumentListResponse,
    DocumentResponse,
    ProcessingStatus,
    UploadResponse,
)
from app.services.processing_pipeline import ProcessingPipeline

logger = logging.getLogger(__name__)


class DocumentService:
    """Facade over upload, processing, and storage subsystems."""

    def __init__(
        self,
        metadata_store: MetadataStore,
        vector_store: VectorStore,
        pipeline: ProcessingPipeline,
    ) -> None:
        self._metadata = metadata_store
        self._vectors = vector_store
        self._pipeline = pipeline

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    async def upload(
        self,
        filename: str,
        content: bytes,
        user_id: int | None = None,
    ) -> str:
        """Validate, persist, and kick off async processing.

        The file is saved in the uploader's own folder (SRS 3.2.2).
        Returns the new document ID.
        """
        self._validate_file(filename, len(content))

        file_hash = hashlib.sha256(content).hexdigest()
        existing_doc = await self._metadata.get_document_by_hash(file_hash, user_id)
        if existing_doc is not None:
            logger.info("Duplicate upload detected '%s' → returning existing %s", filename, existing_doc.id)
            return existing_doc.id

        doc_id = uuid.uuid4().hex
        safe_name = f"{doc_id}_{filename}"
        if user_id is not None:
            safe_name = f"user_{user_id}/{safe_name}"
        dest = settings.upload_dir / safe_name
        dest.parent.mkdir(parents=True, exist_ok=True)

        # Persist to disk
        dest.write_bytes(content)

        # Create metadata record
        document = Document(
            id=doc_id,
            filename=safe_name,
            original_name=filename,
            file_hash=file_hash,
            file_size=len(content),
            status=ProcessingStatus.PENDING,
            user_id=user_id,
        )
        await self._metadata.save_document(document)

        # Fire-and-forget processing (runs in background)
        asyncio.create_task(
            self._run_pipeline(doc_id, str(dest)),
            name=f"process-{doc_id}",
        )

        logger.info("Accepted upload '%s' → %s", filename, doc_id)
        return doc_id

    async def _run_pipeline(self, document_id: str, file_path: str) -> None:
        """Background wrapper around the processing pipeline."""
        try:
            await self._pipeline.process(document_id, file_path)
        except Exception:
            logger.exception("Background processing failed for %s", document_id)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def _owned(self, document_id: str, user_id: int | None) -> Document:
        """The document, if it exists and (when ``user_id`` is given) belongs to that user.
        Another user's document is reported as not found, so its existence is not revealed."""
        doc = await self._metadata.get_document(document_id)
        if doc is None or (user_id is not None and doc.user_id != user_id):
            raise DocumentNotFoundError(document_id)
        return doc

    async def get_document(self, document_id: str, user_id: int | None = None) -> DocumentResponse:
        doc = await self._owned(document_id, user_id)
        return DocumentResponse(**doc.model_dump())

    async def list_documents(
        self, limit: int = 50, offset: int = 0, user_id: int | None = None
    ) -> DocumentListResponse:
        docs, total = await self._metadata.list_documents(limit, offset, user_id=user_id)
        return DocumentListResponse(
            documents=[DocumentResponse(**d.model_dump()) for d in docs],
            total=total,
        )

    async def document_ids_of(self, user_id: int) -> list[str]:
        """IDs of the user's documents that are ready to answer questions from."""
        docs, _ = await self._metadata.list_documents(limit=1000, offset=0, user_id=user_id)
        return [d.id for d in docs if d.status == ProcessingStatus.COMPLETED]

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    async def delete_document(self, document_id: str, user_id: int | None = None) -> None:
        doc = await self._owned(document_id, user_id)

        # Remove vectors
        await self._vectors.delete_by_document(document_id)

        # Remove file from disk
        file_path = settings.upload_dir / doc.filename
        if file_path.exists():
            file_path.unlink()

        # Remove metadata
        await self._metadata.delete_document(document_id)

        logger.info("Deleted document %s (%s)", document_id, doc.original_name)

    # ------------------------------------------------------------------
    # Chunk inspection
    # ------------------------------------------------------------------

    def get_chunks(
        self, document_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Return stored chunks for a document (for debugging / UI inspection)."""
        from app.storage.vector_store import ChromaVectorStore

        if isinstance(self._vectors, ChromaVectorStore):
            return self._vectors.get_chunks_by_document(document_id, limit)
        return []

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_file(filename: str, size: int) -> None:
        ext = Path(filename).suffix.lower()
        if ext not in settings.ALLOWED_EXTENSIONS:
            raise ValidationError(
                "file",
                f"Unsupported file type '{ext}'. Allowed: {settings.ALLOWED_EXTENSIONS}",
            )

        max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
        if size > max_bytes:
            raise ValidationError(
                "file",
                f"File size ({size / 1024 / 1024:.1f}MB) exceeds "
                f"limit ({settings.MAX_FILE_SIZE_MB}MB)",
            )
