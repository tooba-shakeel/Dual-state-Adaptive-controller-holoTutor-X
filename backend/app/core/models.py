"""
Domain models for the PDF RAG application.

These are pure data models with no business logic — they define
the shape of data flowing through the system.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ProcessingStatus(str, enum.Enum):
    """Processing state machine for uploaded documents."""

    PENDING = "pending"
    EXTRACTING = "extracting"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    STORING = "storing"
    COMPLETED = "completed"
    FAILED = "failed"


class ExtractionMethod(str, enum.Enum):
    """How text was obtained from a PDF page."""

    NATIVE = "native"
    OCR = "ocr"
    TABLE = "table"


class InsightType(str, enum.Enum):
    """Types of insights that can be generated."""

    EXECUTIVE_SUMMARY = "executive_summary"
    KEY_INSIGHTS = "key_insights"
    ACTION_ITEMS = "action_items"
    RISKS = "risks"
    RECOMMENDATIONS = "recommendations"
    KPI_METRICS = "kpi_metrics"
    IMPORTANT_DATES = "important_dates"
    NAMED_ENTITIES = "named_entities"
    FINANCIAL_METRICS = "financial_metrics"
    TABLE_INSIGHTS = "table_insights"


# ---------------------------------------------------------------------------
# Core domain models
# ---------------------------------------------------------------------------


class PageContent(BaseModel):
    """Extracted content from a single PDF page."""

    page_number: int
    text: str
    extraction_method: ExtractionMethod = ExtractionMethod.NATIVE
    char_count: int = 0

    def model_post_init(self, __context: Any) -> None:
        if self.char_count == 0:
            self.char_count = len(self.text)


class TableContent(BaseModel):
    """A table extracted from a PDF page."""

    page_number: int
    table_index: int
    markdown: str
    row_count: int
    col_count: int


class ExtractionResult(BaseModel):
    """Complete extraction output from a single PDF."""

    pages: list[PageContent] = Field(default_factory=list)
    tables: list[TableContent] = Field(default_factory=list)
    total_pages: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class Chunk(BaseModel):
    """A text chunk ready for embedding and storage."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    document_id: str
    text: str
    page_number: int
    chunk_index: int
    token_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class Document(BaseModel):
    """Metadata record for an uploaded PDF document."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    filename: str
    original_name: str
    file_hash: str | None = None
    file_size: int = 0  # bytes
    upload_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: ProcessingStatus = ProcessingStatus.PENDING
    page_count: int = 0
    chunk_count: int = 0
    error_message: str | None = None
    user_id: int | None = None  # the student who uploaded it (SRS 3.2.2)


class DocumentInsight(BaseModel):
    """Generated intelligence for a document."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    document_id: str
    insight_type: InsightType
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# API response / request schemas
# ---------------------------------------------------------------------------


class DocumentResponse(BaseModel):
    """Serialization schema for document API responses."""

    id: str
    filename: str
    original_name: str
    file_hash: str | None = None
    file_size: int
    upload_time: datetime
    status: ProcessingStatus
    page_count: int
    chunk_count: int
    error_message: str | None = None
    user_id: int | None = None


class DocumentListResponse(BaseModel):
    """Paginated list of documents."""

    documents: list[DocumentResponse]
    total: int


class ChunkResponse(BaseModel):
    """Serialization schema for chunk inspection."""

    id: str
    document_id: str
    text: str
    page_number: int
    chunk_index: int
    token_count: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class UploadResponse(BaseModel):
    """Response after a successful upload."""

    document_ids: list[str]
    message: str


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str
    components: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    """Standard error response body."""

    error: str
    detail: str | None = None
    request_id: str | None = None


# ---------------------------------------------------------------------------
# Chat & RAG models
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    """A single message in a conversation."""

    role: str  # "user", "assistant", "system"
    content: str


class Citation(BaseModel):
    """Source reference for a retrieved chunk."""

    document_id: str
    document_name: str
    page_number: int
    text: str
    score: float | None = None


class ChatRequest(BaseModel):
    """Request payload for the chat endpoint."""

    conversation_id: str | None = None
    message: str
    document_ids: list[str] | None = None


class ChatResponse(BaseModel):
    """(Used for non-streaming) response for the chat endpoint."""

    answer: str
    citations: list[Citation] = Field(default_factory=list)
    conversation_id: str


class Conversation(BaseModel):
    """Metadata for a chat conversation."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    title: str = "New Conversation"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    messages: list[ChatMessage] = Field(default_factory=list)
    user_id: int | None = None  # the student it belongs to
