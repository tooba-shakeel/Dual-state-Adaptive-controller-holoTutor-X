"""
SQLite-backed store for user accounts and login sessions (SRS 3.2.1, UAM).

Uses the same database file as the other stores (``metadata.db``) and the same
aiosqlite pattern as ``conversation_store.py``. Login tokens are stored only as
SHA-256 hashes, so the database never holds a token that could be reused.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name        TEXT NOT NULL,
    email            TEXT NOT NULL UNIQUE,
    password_hash    TEXT NOT NULL,
    role             TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'active',
    failed_logins    INTEGER NOT NULL DEFAULT 0,
    first_failed_at  TEXT,
    locked_until     TEXT,
    consent_given    INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash    TEXT PRIMARY KEY,
    user_id       INTEGER NOT NULL,
    created_at    TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
"""

_USER_COLUMNS = (
    "id, full_name, email, password_hash, role, status, failed_logins, "
    "first_failed_at, locked_until, consent_given, created_at"
)


def _row_to_user(row: Any) -> dict[str, Any]:
    keys = [c.strip() for c in _USER_COLUMNS.split(",")]
    user = dict(zip(keys, row))
    user["consent_given"] = bool(user["consent_given"])
    return user


class SQLiteUserStore:
    """Users, their roles and status, and their active login sessions."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def initialize(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_CREATE_TABLES_SQL)
            await db.commit()
        logger.info("SQLite user store initialised at %s", self._db_path)

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    async def create_user(
        self, full_name: str, email: str, password_hash: str, role: str, now: datetime
    ) -> dict[str, Any]:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "INSERT INTO users (full_name, email, password_hash, role, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (full_name, email, password_hash, role, now.isoformat()),
            )
            await db.commit()
            user_id = cursor.lastrowid
        user = await self.get_by_id(user_id)
        assert user is not None
        return user

    async def get_by_email(self, email: str) -> dict[str, Any] | None:
        return await self._get_one("SELECT " + _USER_COLUMNS + " FROM users WHERE email = ?", (email,))

    async def get_by_id(self, user_id: int) -> dict[str, Any] | None:
        return await self._get_one("SELECT " + _USER_COLUMNS + " FROM users WHERE id = ?", (user_id,))

    async def list_users(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute("SELECT " + _USER_COLUMNS + " FROM users ORDER BY id")
            rows = await cursor.fetchall()
        return [_row_to_user(r) for r in rows]

    async def has_role(self, role: str) -> bool:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute("SELECT 1 FROM users WHERE role = ? LIMIT 1", (role,))
            return await cursor.fetchone() is not None

    async def update_user(self, user_id: int, **fields: Any) -> None:
        """Update the given columns of one user (column names come from code, never from input)."""
        if not fields:
            return
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = [v.isoformat() if isinstance(v, datetime) else v for v in fields.values()]
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(f"UPDATE users SET {assignments} WHERE id = ?", (*values, user_id))
            await db.commit()

    async def _get_one(self, sql: str, params: tuple) -> dict[str, Any] | None:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(sql, params)
            row = await cursor.fetchone()
        return _row_to_user(row) if row else None

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    async def create_session(self, token_hash: str, user_id: int, now: datetime) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO auth_sessions (token_hash, user_id, created_at, last_seen_at) "
                "VALUES (?, ?, ?, ?)",
                (token_hash, user_id, now.isoformat(), now.isoformat()),
            )
            await db.commit()

    async def get_session(self, token_hash: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT user_id, last_seen_at FROM auth_sessions WHERE token_hash = ?",
                (token_hash,),
            )
            row = await cursor.fetchone()
        if not row:
            return None
        return {"user_id": row[0], "last_seen_at": datetime.fromisoformat(row[1])}

    async def touch_session(self, token_hash: str, now: datetime) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE auth_sessions SET last_seen_at = ? WHERE token_hash = ?",
                (now.isoformat(), token_hash),
            )
            await db.commit()

    async def delete_session(self, token_hash: str) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))
            await db.commit()

    async def delete_sessions_of(self, user_id: int) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
            await db.commit()
