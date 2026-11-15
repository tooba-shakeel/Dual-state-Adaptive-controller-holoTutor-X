"""
Dependency injection container.

Creates singleton instances of services at startup and provides
getter functions used by API routes.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import settings
from app.services.chunker import RecursiveChunker
from app.services.document_service import DocumentService
from app.services.embedding_service import SentenceTransformerEmbedder
from app.services.pdf_extractor import PyMuPDFExtractor
from app.services.processing_pipeline import ProcessingPipeline
from app.storage.metadata_store import SQLiteMetadataStore
from app.storage.vector_store import ChromaVectorStore

from app.storage.conversation_store import SQLiteConversationStore
from app.services.llm_service import OpenAILLMProvider
from app.services.retriever_service import RetrieverService
from app.services.prompt_builder import PromptBuilder
from app.services.chat_service import ChatService
from app.services.insights_service import InsightsService
from app.services.evidence_assessor import EvidenceAssessor
from app.services.decision_controller import DecisionController
from app.services.learner_state import LearnerStateEstimator
from app.storage.signal_store import SQLiteSignalStore
from app.storage.user_store import SQLiteUserStore
from app.services.auth_service import AuthService

logger = logging.getLogger(__name__)

# ── Singleton instances (initialised in ``startup()``) ───────────────
_metadata_store: SQLiteMetadataStore | None = None
_vector_store: ChromaVectorStore | None = None
_embedder: SentenceTransformerEmbedder | None = None
_document_service: DocumentService | None = None
_conversation_store: SQLiteConversationStore | None = None
_llm_provider: OpenAILLMProvider | None = None
_chat_service: ChatService | None = None
_insights_service: InsightsService | None = None
_auth_service: AuthService | None = None


async def startup() -> None:
    """Initialise all singletons.  Called once from ``main.py``."""
    global _metadata_store, _vector_store, _embedder, _document_service
    global _conversation_store, _llm_provider, _chat_service, _insights_service
    global _auth_service

    logger.info("Initialising services …")

    # Storage
    _metadata_store = SQLiteMetadataStore(str(settings.metadata_db_path))
    await _metadata_store.initialize()

    # Accounts (UAM)
    user_store = SQLiteUserStore(str(settings.metadata_db_path))
    await user_store.initialize()
    _auth_service = AuthService(
        user_store,
        token_idle_minutes=settings.TOKEN_IDLE_MINUTES,
        max_failed_logins=settings.MAX_FAILED_LOGINS,
        failed_login_window_minutes=settings.FAILED_LOGIN_WINDOW_MINUTES,
        lock_minutes=settings.LOCK_MINUTES,
        password_min_length=settings.PASSWORD_MIN_LENGTH,
    )
    await _auth_service.ensure_administrator(
        settings.ADMIN_NAME, settings.ADMIN_EMAIL, settings.ADMIN_PASSWORD
    )

    _conversation_store = SQLiteConversationStore(str(settings.metadata_db_path))
    await _conversation_store.initialize()

    _vector_store = ChromaVectorStore(str(settings.chroma_dir))

    # Embedding model
    _embedder = SentenceTransformerEmbedder(
        model_name=settings.EMBEDDING_MODEL,
        batch_size=settings.EMBEDDING_BATCH_SIZE,
    )

    # LLM Provider
    _llm_provider = OpenAILLMProvider(
        api_key=settings.OPENAI_API_KEY,
        model=settings.LLM_MODEL,
        base_url=settings.OPENAI_API_BASE,
        timeout=settings.LLM_TIMEOUT_SECONDS,
    )

    # Services
    extractor = PyMuPDFExtractor()
    chunker = RecursiveChunker(
        chunk_size=settings.CHUNK_SIZE,
        chunk_overlap=settings.CHUNK_OVERLAP,
    )

    pipeline = ProcessingPipeline(
        extractor=extractor,
        chunker=chunker,
        embedder=_embedder,
        vector_store=_vector_store,
        metadata_store=_metadata_store,
    )

    _document_service = DocumentService(
        metadata_store=_metadata_store,
        vector_store=_vector_store,
        pipeline=pipeline,
    )

    # RAG Services
    retriever = RetrieverService(
        vector_store=_vector_store,
        metadata_store=_metadata_store,
        embedder=_embedder,
        similarity_threshold=settings.SIMILARITY_THRESHOLD,
    )
    prompt_builder = PromptBuilder()

    # Dual-State tutoring
    evidence_assessor = EvidenceAssessor(
        llm=_llm_provider,
        partial_limit=settings.PARTIAL_LIMIT,
        strong_limit=settings.STRONG_LIMIT,
    )
    decision_controller = DecisionController.from_file(settings.DECISION_TABLE_PATH)
       # LSE: learner state from the student's last 3 rating signals (SRS 3.2.4)
    learner_estimator = LearnerStateEstimator(SQLiteSignalStore(str(settings.metadata_db_path)))

    _chat_service = ChatService(
        retriever=retriever,
        prompt_builder=prompt_builder,
        llm=_llm_provider,
        conversation_store=_conversation_store,
        evidence_assessor=evidence_assessor,
        learner_estimator=learner_estimator,
        decision_controller=decision_controller,
        top_k=settings.TOP_K_RETRIEVAL,
    )

    _insights_service = InsightsService(
        metadata_store=_metadata_store,
        llm=_llm_provider,
        vector_store=_vector_store,
    )

    logger.info("All services initialised ✓")


async def shutdown() -> None:
    """Cleanup on application shutdown."""
    logger.info("Shutting down services …")
    # ChromaDB and SQLite handle their own cleanup; nothing explicit needed.


# ── Getters (used by routes) ─────────────────────────────────────────

def get_auth_service() -> AuthService:
    assert _auth_service is not None, "Auth service not initialised"
    return _auth_service


def get_metadata_store() -> SQLiteMetadataStore:
    assert _metadata_store is not None, "Metadata store not initialised"
    return _metadata_store


def get_vector_store() -> ChromaVectorStore:
    assert _vector_store is not None, "Vector store not initialised"
    return _vector_store


def get_embedder() -> SentenceTransformerEmbedder:
    assert _embedder is not None, "Embedder not initialised"
    return _embedder


def get_document_service() -> DocumentService:
    assert _document_service is not None, "Document service not initialised"
    return _document_service


def get_conversation_store() -> SQLiteConversationStore:
    assert _conversation_store is not None, "Conversation store not initialised"
    return _conversation_store


def get_chat_service() -> ChatService:
    assert _chat_service is not None, "Chat service not initialised"
    return _chat_service

def get_insights_service() -> InsightsService:
    assert _insights_service is not None, "Insights service not initialised"
    return _insights_service
