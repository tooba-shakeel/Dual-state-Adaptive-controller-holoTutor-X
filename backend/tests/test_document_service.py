"""
Tests for document deduplication logic in DocumentService.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.models import Document, ProcessingStatus
from app.services.document_service import DocumentService


@pytest.fixture
def mock_metadata_store():
    store = AsyncMock()
    store.get_document_by_hash.return_value = None
    store.save_document = AsyncMock()
    store.get_document.return_value = Document(
        id="doc1", filename="test.pdf", original_name="test.pdf",
        file_size=100, status=ProcessingStatus.COMPLETED
    )
    store.delete_document = AsyncMock()
    store.update_status = AsyncMock()
    return store


@pytest.fixture
def mock_vector_store():
    store = AsyncMock()
    store.delete_by_document = AsyncMock()
    return store


@pytest.fixture
def mock_pipeline():
    return AsyncMock()


@pytest.mark.asyncio
async def test_upload_returns_existing_id_for_duplicate(mock_metadata_store, mock_vector_store, mock_pipeline):
    """When a file with the same hash already exists, return the existing ID."""
    existing_doc = Document(
        id="existing_id",
        filename="existing_test.pdf",
        original_name="test.pdf",
        file_hash="abc123",
        file_size=100,
        status=ProcessingStatus.COMPLETED,
    )
    mock_metadata_store.get_document_by_hash.return_value = existing_doc

    service = DocumentService(
        metadata_store=mock_metadata_store,
        vector_store=mock_vector_store,
        pipeline=mock_pipeline,
    )

    with patch.object(service, '_validate_file'):
        doc_id = await service.upload("test.pdf", b"test content")

    assert doc_id == "existing_id"
    # Should NOT have saved a new document
    mock_metadata_store.save_document.assert_not_called()


@pytest.mark.asyncio
async def test_upload_creates_new_for_unique_file(mock_metadata_store, mock_vector_store, mock_pipeline, tmp_path):
    """When the file hash is new, create and process a new document."""
    mock_metadata_store.get_document_by_hash.return_value = None

    service = DocumentService(
        metadata_store=mock_metadata_store,
        vector_store=mock_vector_store,
        pipeline=mock_pipeline,
    )

    from app.config import settings
    # upload_dir is a computed property, so point its base folder at tmp_path instead.
    with patch.object(settings, 'DATA_DIR', str(tmp_path)):
        with patch.object(service, '_validate_file'):
            doc_id = await service.upload("test.pdf", b"unique content")

    assert doc_id is not None
    assert len(doc_id) == 32  # uuid hex length
    mock_metadata_store.save_document.assert_called_once()


@pytest.mark.asyncio
async def test_delete_document(mock_metadata_store, mock_vector_store, mock_pipeline, tmp_path):
    """Delete should remove vectors, file, and metadata."""
    doc = Document(
        id="doc1", filename="test.pdf", original_name="test.pdf",
        file_size=100, status=ProcessingStatus.COMPLETED
    )
    mock_metadata_store.get_document.return_value = doc

    service = DocumentService(
        metadata_store=mock_metadata_store,
        vector_store=mock_vector_store,
        pipeline=mock_pipeline,
    )

    from app.config import settings
    # upload_dir is a computed property, so point its base folder at tmp_path instead.
    with patch.object(settings, 'DATA_DIR', str(tmp_path)):
        # Create a dummy file
        (settings.upload_dir / "test.pdf").touch()
        await service.delete_document("doc1")

    mock_vector_store.delete_by_document.assert_called_once_with("doc1")
    mock_metadata_store.delete_document.assert_called_once_with("doc1")
