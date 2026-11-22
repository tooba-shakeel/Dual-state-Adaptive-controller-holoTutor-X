"""
SQLite-backed store for learner rating signals (Got it / Not sure / Confused).

Uses the same database file as the other stores (``metadata.db``) and the same
aiosqlite pattern as ``conversation_store.py``.  Used by the LSE module
(SRS 3.2.4).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS learner_signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    topic           TEXT NOT NULL DEFAULT '',
    signal          INTEGER NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_learner_signals_conv
    ON learner_signals (conversation_id, topic, id);
"""


class SQLiteSignalStore:
    """Stores +1 / 0 / -1 rating signals per conversation and topic."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._initialized = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_CREATE_TABLE_SQL)
            await db.commit()
        self._initialized = True
        logger.info("SQLite signal store initialised at %s", self._db_path)

    async def _ensure_initialized(self) -> None:
        if not self._initialized:
            await self.initialize()

    # ------------------------------------------------------------------
    # Read / write
    # ------------------------------------------------------------------

    async def add_signal(
        self, conversation_id: str, signal: int, topic: str | None = None
    ) -> None:
        await self._ensure_initialized()
        now_str = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO learner_signals (conversation_id, topic, signal, created_at) "
                "VALUES (?, ?, ?, ?)",
                (conversation_id, topic or "", signal, now_str),
            )
            await db.commit()

    async def get_recent_signals(
        self,
        conversation_id: str,
        topic: str | None = None,
        limit: int = 3,
    ) -> list[int]:
        """Return up to ``limit`` signals, newest first.

        If ``topic`` is given, only signals for that topic are returned.
        If ``topic`` is None, signals of the whole conversation are returned.
        """
        await self._ensure_initialized()
        async with aiosqlite.connect(self._db_path) as db:
            if topic is None:
                cursor = await db.execute(
                    "SELECT signal FROM learner_signals "
                    "WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
                    (conversation_id, limit),
                )
            else:
                cursor = await db.execute(
                    "SELECT signal FROM learner_signals "
                    "WHERE conversation_id = ? AND topic = ? ORDER BY id DESC LIMIT ?",
                    (conversation_id, topic, limit),
                )
            rows = await cursor.fetchall()
        return [int(r[0]) for r in rows]
