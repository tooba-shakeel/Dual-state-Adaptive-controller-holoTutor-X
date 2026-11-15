"""
Document management endpoints — upload, list, get, delete, chunk inspection.

Every route needs a login. Each user uploads, sees and deletes only their own
documents (SRS 3.2.2); another user's document is reported as not found.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

from app.api.routes.auth import current_user
from app.core.models import (
    ChunkResponse,
    DocumentListResponse,
    DocumentResponse,
    UploadResponse,
    InsightType,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])


def _get_document_service():
    """Lazy import to avoid circular dependency."""
    from app.dependencies import get_document_service
    return get_document_service()

def _get_insights_service():
    from app.dependencies import get_insights_service
    return get_insights_service()


# ------------------------------------------------------------------
# Upload
# ------------------------------------------------------------------

@router.post("/upload", response_model=UploadResponse)
async def upload_documents(
    files: list[UploadFile] = File(..., description="One or more PDF files"),
    user: dict[str, Any] = Depends(current_user),
):
    """Upload one or more PDF files for processing, owned by the logged-in user."""
    service = _get_document_service()
    document_ids: list[str] = []

    for upload_file in files:
        content = await upload_file.read()
        doc_id = await service.upload(
            filename=upload_file.filename or "unknown.pdf",
            content=content,
            user_id=user["id"],
        )
        document_ids.append(doc_id)

    return UploadResponse(
        document_ids=document_ids,
        message=f"Accepted {len(document_ids)} file(s) for processing",
    )


# ------------------------------------------------------------------
# List
# ------------------------------------------------------------------

@router.get("", response_model=DocumentListResponse)
async def list_documents(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict[str, Any] = Depends(current_user),
):
    """List the logged-in user's documents with their processing status."""
    service = _get_document_service()
    return await service.list_documents(limit=limit, offset=offset, user_id=user["id"])


# ------------------------------------------------------------------
# Get
# ------------------------------------------------------------------

@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(document_id: str, user: dict[str, Any] = Depends(current_user)):
    """Get a single document's metadata and processing status."""
    service = _get_document_service()
    return await service.get_document(document_id, user_id=user["id"])


# ------------------------------------------------------------------
# Delete
# ------------------------------------------------------------------

@router.delete("/{document_id}")
async def delete_document(document_id: str, user: dict[str, Any] = Depends(current_user)):
    """Delete a document and all associated data (file, vectors, metadata)."""
    service = _get_document_service()
    await service.delete_document(document_id, user_id=user["id"])
    return {"message": f"Document {document_id} deleted successfully"}


# ------------------------------------------------------------------
# Chunk inspection
# ------------------------------------------------------------------

@router.get("/{document_id}/chunks", response_model=list[ChunkResponse])
async def get_document_chunks(
    document_id: str,
    limit: int = Query(100, ge=1, le=500),
    user: dict[str, Any] = Depends(current_user),
):
    """List stored chunks for a document (for debugging / inspection)."""
    service = _get_document_service()

    # Verify the document exists and belongs to the user
    await service.get_document(document_id, user_id=user["id"])

    raw_chunks = service.get_chunks(document_id, limit=limit)
    return [
        ChunkResponse(
            id=c["id"],
            document_id=c.get("metadata", {}).get("document_id", document_id),
            text=c.get("text", ""),
            page_number=c.get("metadata", {}).get("page_number", 0),
            chunk_index=c.get("metadata", {}).get("chunk_index", 0),
            token_count=c.get("metadata", {}).get("token_count", 0),
            metadata=c.get("metadata", {}),
        )
        for c in raw_chunks
    ]

# ------------------------------------------------------------------
# Insights
# ------------------------------------------------------------------

@router.get("/{document_id}/insights/{insight_type}")
async def get_document_insight(
    document_id: str, insight_type: InsightType, user: dict[str, Any] = Depends(current_user)
):
    """Get or generate a specific insight for a document."""
    service = _get_insights_service()
    doc_service = _get_document_service()

    # Ensure the document exists and belongs to the user
    await doc_service.get_document(document_id, user_id=user["id"])

    insight = await service.get_or_generate_insight(document_id, insight_type)
    return insight


class CompareRequest(BaseModel):
    document_ids: list[str]
    query: str = "Compare these documents."

@router.post("/compare")
async def compare_documents(request: CompareRequest, user: dict[str, Any] = Depends(current_user)):
    """Compare multiple documents based on a query."""
    if len(request.document_ids) < 2:
        raise HTTPException(status_code=400, detail="At least 2 documents are required for comparison.")

    doc_service = _get_document_service()
    for document_id in request.document_ids:
        await doc_service.get_document(document_id, user_id=user["id"])

    service = _get_insights_service()
    result = await service.compare_documents(request.document_ids, request.query)
    return {"comparison": result}
