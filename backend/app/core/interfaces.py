"""
Abstract base classes (interfaces) for all pluggable components.

These define the contracts that concrete implementations must satisfy.
Depend on these interfaces — never on concrete classes — to keep the
architecture flexible and testable.
"""

from __future__ import annotations

import typing
from abc import ABC, abstractmethod
from typing import Any

from app.core.models import (
    Chunk,
    Document,
    DocumentInsight,
    InsightType,
    ExtractionResult,
    ProcessingStatus,
    ChatMessage,
    Conversation,
)


class PDFExtractor(ABC):
    """Contract for extracting text (and tables) from PDF files."""

    @abstractmethod
    async def extract(self, file_path: str) -> ExtractionResult:
        """Extract all textual content from *file_path* and return structured results."""
        ...


class TextChunker(ABC):
    """Contract for splitting extracted text into embeddable chunks."""

    @abstractmethod
    def chunk(
        self,
        text: str,
        document_id: str,
        page_number: int,
        start_index: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> list[Chunk]:
        """Split *text* into a list of ``Chunk`` objects."""
        ...


class EmbeddingProvider(ABC):
    """Contract for generating vector embeddings from text."""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return embedding vectors for each text in *texts*."""
        ...

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Dimensionality of the embedding vectors."""
        ...


class VectorStore(ABC):
    """Contract for persisting and querying vector embeddings."""

    @abstractmethod
    async def add(
        self,
        chunks: list[Chunk],
        embeddings: list[list[float]],
    ) -> None:
        """Store *chunks* with their corresponding *embeddings*."""
        ...

    @abstractmethod
    async def search(
        self,
        query_embedding: list[float],
        k: int = 5,
        filter_metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Return the top-*k* most similar chunks."""
        ...

    @abstractmethod
    async def delete_by_document(self, document_id: str) -> None:
        """Remove all vectors associated with *document_id*."""
        ...


class MetadataStore(ABC):
    """Contract for persisting document metadata (non-vector data)."""

    @abstractmethod
    async def initialize(self) -> None:
        """Create tables / ensure schema exists."""
        ...

    @abstractmethod
    async def save_document(self, document: Document) -> None:
        """Insert or update a document record."""
        ...

    @abstractmethod
    async def get_document(self, document_id: str) -> Document | None:
        """Retrieve a document by ID, or ``None`` if not found."""
        ...

    @abstractmethod
    async def list_documents(
        self, limit: int = 50, offset: int = 0, user_id: int | None = None
    ) -> tuple[list[Document], int]:
        """Return a page of documents and the total count (only one user's when ``user_id`` is given)."""
        ...

    @abstractmethod
    async def update_status(
        self,
        document_id: str,
        status: ProcessingStatus,
        error_message: str | None = None,
        page_count: int | None = None,
        chunk_count: int | None = None,
    ) -> None:
        """Update the processing status (and optional counters) for a document."""
        ...

    @abstractmethod
    async def delete_document(self, document_id: str) -> None:
        """Delete a document record."""
        ...

    @abstractmethod
    async def get_document_by_hash(self, file_hash: str, user_id: int | None = None) -> Document | None:
        """Retrieve a document by its file hash (among one user's documents when ``user_id`` is given)."""
        ...

    @abstractmethod
    async def save_insight(self, insight: DocumentInsight) -> None:
        """Save a generated insight."""
        ...

    @abstractmethod
    async def get_insight(self, document_id: str, insight_type: InsightType) -> DocumentInsight | None:
        """Retrieve a cached insight."""
        ...


class LLMProvider(ABC):
    """Contract for generating text from an LLM."""

    @abstractmethod
    async def generate_stream(
        self, messages: list[ChatMessage], context: str
    ) -> typing.AsyncGenerator[str, None]:
        """Generate a response as a stream of text chunks."""
        ...


class ConversationStore(ABC):
    """Contract for persisting chat history."""

    @abstractmethod
    async def initialize(self) -> None:
        """Create tables / ensure schema exists."""
        ...

    @abstractmethod
    async def create_conversation(
        self, title: str = "New Conversation", user_id: int | None = None
    ) -> Conversation:
        """Create a new conversation owned by ``user_id``."""
        ...

    @abstractmethod
    async def get_conversation(self, conversation_id: str) -> Conversation | None:
        """Retrieve a conversation by ID."""
        ...

    @abstractmethod
    async def list_conversations(
        self, limit: int = 50, offset: int = 0, user_id: int | None = None
    ) -> tuple[list[Conversation], int]:
        """List conversations (only one user's when ``user_id`` is given)."""
        ...

    @abstractmethod
    async def add_message(self, conversation_id: str, message: ChatMessage) -> None:
        """Add a message to a conversation."""
        ...

    @abstractmethod
    async def update_title(self, conversation_id: str, title: str) -> None:
        """Rename a conversation."""
        ...

    @abstractmethod
    async def delete_conversation(self, conversation_id: str) -> None:
        """Delete a conversation."""
        ...
