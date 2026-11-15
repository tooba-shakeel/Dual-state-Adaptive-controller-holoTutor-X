"""
SQLite-backed conversation store using aiosqlite.

Stores chat conversations and messages.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
import uuid

import aiosqlite

from app.core.interfaces import ConversationStore
from app.core.models import Conversation, ChatMessage

logger = logging.getLogger(__name__)

_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
"""


class SQLiteConversationStore(ConversationStore):
    """Concrete ``ConversationStore`` backed by SQLite."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_CREATE_TABLES_SQL)
            # Owner of the conversation (SRS 3.2.8: a student sees only their own sessions)
            try:
                await db.execute("ALTER TABLE conversations ADD COLUMN user_id INTEGER")
            except aiosqlite.OperationalError:
                pass
            await db.commit()
        logger.info("SQLite conversation store initialized.")

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------

    async def create_conversation(
        self, title: str = "New Conversation", user_id: int | None = None
    ) -> Conversation:
        now_str = datetime.now(timezone.utc).isoformat()
        conv = Conversation(title=title, user_id=user_id)
        conv.created_at = datetime.fromisoformat(now_str)
        conv.updated_at = datetime.fromisoformat(now_str)

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO conversations (id, title, created_at, updated_at, user_id) VALUES (?, ?, ?, ?, ?)",
                (conv.id, conv.title, now_str, now_str, user_id),
            )
            await db.commit()
        return conv

    async def get_conversation(self, conversation_id: str) -> Conversation | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,))
            row = await cursor.fetchone()
            if not row:
                return None

            msg_cursor = await db.execute(
                "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
                (conversation_id,),
            )
            msg_rows = await msg_cursor.fetchall()

        messages = [ChatMessage(role=r["role"], content=r["content"]) for r in msg_rows]

        return Conversation(
            id=row["id"],
            title=row["title"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            messages=messages,
            user_id=row["user_id"],
        )

    async def list_conversations(
        self, limit: int = 50, offset: int = 0, user_id: int | None = None
    ) -> tuple[list[Conversation], int]:
        where, params = ("WHERE user_id = ?", (user_id,)) if user_id is not None else ("", ())
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row

            cursor = await db.execute(f"SELECT COUNT(*) as cnt FROM conversations {where}", params)
            total_row = await cursor.fetchone()
            total = total_row["cnt"] if total_row else 0

            cursor = await db.execute(
                f"SELECT * FROM conversations {where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            )
            rows = await cursor.fetchall()

        conversations = [
            Conversation(
                id=r["id"],
                title=r["title"],
                created_at=datetime.fromisoformat(r["created_at"]),
                updated_at=datetime.fromisoformat(r["updated_at"]),
                user_id=r["user_id"],
            )
            for r in rows
        ]
        return conversations, total

    async def add_message(self, conversation_id: str, message: ChatMessage) -> None:
        msg_id = uuid.uuid4().hex
        now_str = datetime.now(timezone.utc).isoformat()
        
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO messages (id, conversation_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (msg_id, conversation_id, message.role, message.content, now_str),
            )
            await db.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now_str, conversation_id),
            )
            await db.commit()

    async def delete_conversation(self, conversation_id: str) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            # Foreign key cascade delete will remove messages
            await db.execute("PRAGMA foreign_keys = ON;")
            await db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
            await db.commit()
