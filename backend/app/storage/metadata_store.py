"""
SQLite-backed metadata store using aiosqlite.

Stores document-level metadata (status, page counts, timestamps)
separate from the vector data.  Schema is auto-created on first run.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import aiosqlite

from app.core.interfaces import MetadataStore
from app.core.models import Document, ProcessingStatus, DocumentInsight, InsightType

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS documents (
    id              TEXT PRIMARY KEY,
    filename        TEXT NOT NULL,
    original_name   TEXT NOT NULL,
    file_hash       TEXT,
    file_size       INTEGER NOT NULL DEFAULT 0,
    upload_time     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    page_count      INTEGER NOT NULL DEFAULT 0,
    chunk_count     INTEGER NOT NULL DEFAULT 0,
    error_message   TEXT
);

CREATE TABLE IF NOT EXISTS document_insights (
    id              TEXT PRIMARY KEY,
    document_id     TEXT NOT NULL,
    insight_type    TEXT NOT NULL,
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    metadata        TEXT NOT NULL,
    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE,
    UNIQUE(document_id, insight_type)
);
"""


class SQLiteMetadataStore(MetadataStore):
    """Concrete ``MetadataStore`` backed by a local SQLite file."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._initialized = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_CREATE_TABLE_SQL)
            
            try:
                await db.execute("ALTER TABLE documents ADD COLUMN file_hash TEXT")
            except aiosqlite.OperationalError:
                pass

            # Owner of the document (SRS 3.2.2: each student sees only their own documents)
            try:
                await db.execute("ALTER TABLE documents ADD COLUMN user_id INTEGER")
            except aiosqlite.OperationalError:
                pass

            await db.commit()
        self._initialized = True
        logger.info("SQLite metadata store initialised at %s", self._db_path)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def save_document(self, document: Document) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO documents
                    (id, filename, original_name, file_hash, file_size, upload_time,
                     status, page_count, chunk_count, error_message, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document.id,
                    document.filename,
                    document.original_name,
                    document.file_hash,
                    document.file_size,
                    document.upload_time.isoformat(),
                    document.status.value,
                    document.page_count,
                    document.chunk_count,
                    document.error_message,
                    document.user_id,
                ),
            )
            await db.commit()

    async def get_document(self, document_id: str) -> Document | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM documents WHERE id = ?", (document_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return self._row_to_document(row)

    async def list_documents(
        self, limit: int = 50, offset: int = 0, user_id: int | None = None
    ) -> tuple[list[Document], int]:
        where, params = ("WHERE user_id = ?", (user_id,)) if user_id is not None else ("", ())
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row

            # Total count
            cursor = await db.execute(f"SELECT COUNT(*) as cnt FROM documents {where}", params)
            total_row = await cursor.fetchone()
            total = total_row["cnt"] if total_row else 0

            # Paginated results
            cursor = await db.execute(
                f"SELECT * FROM documents {where} ORDER BY upload_time DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            )
            rows = await cursor.fetchall()
            documents = [self._row_to_document(r) for r in rows]
            return documents, total

    async def update_status(
        self,
        document_id: str,
        status: ProcessingStatus,
        error_message: str | None = None,
        page_count: int | None = None,
        chunk_count: int | None = None,
    ) -> None:
        parts: list[str] = ["status = ?"]
        params: list[object] = [status.value]

        if error_message is not None:
            parts.append("error_message = ?")
            params.append(error_message)
        if page_count is not None:
            parts.append("page_count = ?")
            params.append(page_count)
        if chunk_count is not None:
            parts.append("chunk_count = ?")
            params.append(chunk_count)

        params.append(document_id)

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                f"UPDATE documents SET {', '.join(parts)} WHERE id = ?",
                tuple(params),
            )
            await db.commit()

    async def delete_document(self, document_id: str) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("DELETE FROM documents WHERE id = ?", (document_id,))
            await db.commit()

    async def get_document_by_hash(self, file_hash: str, user_id: int | None = None) -> Document | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            if user_id is None:
                cursor = await db.execute(
                    "SELECT * FROM documents WHERE file_hash = ?", (file_hash,)
                )
            else:  # the same PDF uploaded by another student is a separate document
                cursor = await db.execute(
                    "SELECT * FROM documents WHERE file_hash = ? AND user_id = ?", (file_hash, user_id)
                )
            row = await cursor.fetchone()
            if row is None:
                return None
            return self._row_to_document(row)

    async def save_insight(self, insight: DocumentInsight) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO document_insights
                    (id, document_id, insight_type, content, created_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    insight.id,
                    insight.document_id,
                    insight.insight_type.value,
                    insight.content,
                    insight.created_at.isoformat(),
                    json.dumps(insight.metadata),
                ),
            )
            await db.commit()

    async def get_insight(self, document_id: str, insight_type: InsightType) -> DocumentInsight | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM document_insights WHERE document_id = ? AND insight_type = ?",
                (document_id, insight_type.value),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return DocumentInsight(
                id=row["id"],
                document_id=row["document_id"],
                insight_type=InsightType(row["insight_type"]),
                content=row["content"],
                created_at=datetime.fromisoformat(row["created_at"]),
                metadata=json.loads(row["metadata"]),
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_document(row: aiosqlite.Row) -> Document:
        file_hash = row["file_hash"] if "file_hash" in row.keys() else None
        user_id = row["user_id"] if "user_id" in row.keys() else None
        return Document(
            id=row["id"],
            filename=row["filename"],
            original_name=row["original_name"],
            file_hash=file_hash,
            file_size=row["file_size"],
            upload_time=datetime.fromisoformat(row["upload_time"]),
            status=ProcessingStatus(row["status"]),
            page_count=row["page_count"],
            chunk_count=row["chunk_count"],
            error_message=row["error_message"],
            user_id=user_id,
        )
