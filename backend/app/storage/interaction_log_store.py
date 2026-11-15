"""
SQLite-backed interaction log (SRS LOG module).

One row per question a student asks: who asked, the Tutor State the
controller produced (learner state, evidence state and score, teaching action,
decision-table version) and whether the student got an answer.

The administrator's Logs and Unanswered screens read from this table.
Uses the same database file (``metadata.db``) and aiosqlite pattern as the other stores.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS interaction_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at       TEXT NOT NULL,
    user_id          INTEGER,
    conversation_id  TEXT NOT NULL,
    question         TEXT NOT NULL,
    topic            TEXT,
    learner_state    TEXT NOT NULL,
    evidence_state   TEXT NOT NULL,
    evidence_score   REAL NOT NULL,
    teaching_action  TEXT NOT NULL,
    table_version    TEXT,
    answered         INTEGER NOT NULL,
    answer_chars     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_interaction_log_user ON interaction_log (user_id, id);
CREATE INDEX IF NOT EXISTS idx_interaction_log_answered ON interaction_log (answered, id);
"""

_COLUMNS = (
    "l.id, l.created_at, l.user_id, u.email AS user_email, l.conversation_id, l.question, "
    "l.topic, l.learner_state, l.evidence_state, l.evidence_score, l.teaching_action, "
    "l.table_version, l.answered, l.answer_chars"
)


class SQLiteInteractionLogStore:
    """Writes and reads interaction-log rows."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._initialized = False

    async def initialize(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_CREATE_TABLE_SQL)
            await db.commit()
        self._initialized = True
        logger.info("SQLite interaction log initialised at %s", self._db_path)

    async def _ensure_initialized(self) -> None:
        if not self._initialized:
            await self.initialize()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def add(
        self,
        *,
        user_id: int | None,
        conversation_id: str,
        question: str,
        topic: str | None,
        learner_state: str,
        evidence_state: str,
        evidence_score: float,
        teaching_action: str,
        table_version: str | None,
        answered: bool,
        answer_chars: int = 0,
    ) -> None:
        await self._ensure_initialized()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO interaction_log (created_at, user_id, conversation_id, question, topic, "
                "learner_state, evidence_state, evidence_score, teaching_action, table_version, "
                "answered, answer_chars) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    user_id,
                    conversation_id,
                    question,
                    topic,
                    learner_state,
                    evidence_state,
                    float(evidence_score),
                    teaching_action,
                    table_version,
                    1 if answered else 0,
                    answer_chars,
                ),
            )
            await db.commit()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def list_entries(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        user_id: int | None = None,
        unanswered_only: bool = False,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Newest first. ``date_from`` / ``date_to`` are ISO dates (YYYY-MM-DD), both inclusive."""
        await self._ensure_initialized()
        conditions: list[str] = []
        params: list[Any] = []
        if user_id is not None:
            conditions.append("l.user_id = ?")
            params.append(user_id)
        if unanswered_only:
            conditions.append("l.answered = 0")
        if date_from:
            conditions.append("substr(l.created_at, 1, 10) >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("substr(l.created_at, 1, 10) <= ?")
            params.append(date_to)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(f"SELECT COUNT(*) FROM interaction_log l {where}", params)
            total = (await cursor.fetchone())[0]
            cursor = await db.execute(
                f"SELECT {_COLUMNS} FROM interaction_log l LEFT JOIN users u ON u.id = l.user_id "
                f"{where} ORDER BY l.id DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            )
            rows = await cursor.fetchall()

        items = []
        for r in rows:
            item = dict(r)
            item["answered"] = bool(item["answered"])
            items.append(item)
        return items, total
