"""
User Account Management (UAM) — sign up, login, logout, roles and account status (SRS 3.2.1).

Rules from the SRS:
- Sign up always creates a Student. Evaluators are created by the administrator;
  the administrator account is created when the system is installed.
- The password has at least PASSWORD_MIN_LENGTH characters and is stored only as a hash.
- A wrong e-mail or password shows one general message. After MAX_FAILED_LOGINS wrong
  tries within FAILED_LOGIN_WINDOW_MINUTES the account is locked for LOCK_MINUTES.
- A disabled account cannot log in.
- The login token stops working after TOKEN_IDLE_MINUTES without activity, and at once on logout.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.core.exceptions import (
    AccountLockedError,
    AuthenticationError,
    ConflictError,
    PermissionDeniedError,
    ValidationError,
)
from app.storage.user_store import SQLiteUserStore

logger = logging.getLogger(__name__)

STUDENT = "student"
EVALUATOR = "evaluator"
ADMINISTRATOR = "administrator"
ROLES = (STUDENT, EVALUATOR, ADMINISTRATOR)

ACTIVE = "active"
DISABLED = "disabled"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PBKDF2_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    """PBKDF2-SHA256 with a random salt, stored as 'pbkdf2_sha256$iterations$salt$hash'."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if scheme != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
    )
    return hmac.compare_digest(digest.hex(), digest_hex)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    """The fields of a user that may be sent to the browser (never the password hash)."""
    return {
        "id": user["id"],
        "full_name": user["full_name"],
        "email": user["email"],
        "role": user["role"],
        "status": user["status"],
        "consent_given": user["consent_given"],
        "created_at": user["created_at"],
    }


class AuthService:
    """Accounts, login sessions and role checks."""

    def __init__(
        self,
        store: SQLiteUserStore,
        token_idle_minutes: int = 30,
        max_failed_logins: int = 5,
        failed_login_window_minutes: int = 10,
        lock_minutes: int = 5,
        password_min_length: int = 8,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._idle = timedelta(minutes=token_idle_minutes)
        self._max_failed = max_failed_logins
        self._failed_window = timedelta(minutes=failed_login_window_minutes)
        self._lock = timedelta(minutes=lock_minutes)
        self._lock_minutes = lock_minutes
        self._min_length = password_min_length
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # ------------------------------------------------------------------
    # Accounts
    # ------------------------------------------------------------------

    async def sign_up(self, full_name: str, email: str, password: str) -> dict[str, Any]:
        """A new student creates an account (sign up always gives the Student role)."""
        return await self._create_account(full_name, email, password, STUDENT)

    async def create_evaluator(
        self, admin: dict[str, Any], full_name: str, email: str, password: str
    ) -> dict[str, Any]:
        self.require_role(admin, ADMINISTRATOR)
        return await self._create_account(full_name, email, password, EVALUATOR)

    async def ensure_administrator(self, full_name: str, email: str, password: str) -> None:
        """Create the administrator at install time, once, if none exists yet."""
        if not email or not password:
            if not await self._store.has_role(ADMINISTRATOR):
                logger.warning("No administrator account: set ADMIN_EMAIL and ADMIN_PASSWORD in .env")
            return
        if await self._store.has_role(ADMINISTRATOR):
            return
        await self._create_account(full_name or "Administrator", email, password, ADMINISTRATOR)
        logger.info("Administrator account created for %s", email.strip().lower())

    async def list_users(self, admin: dict[str, Any]) -> list[dict[str, Any]]:
        self.require_role(admin, ADMINISTRATOR)
        return [public_user(u) for u in await self._store.list_users()]

    async def set_status(self, admin: dict[str, Any], user_id: int, status: str) -> dict[str, Any]:
        """The administrator disables or enables an account."""
        self.require_role(admin, ADMINISTRATOR)
        if status not in (ACTIVE, DISABLED):
            raise ValidationError("status", "Status must be 'active' or 'disabled'")
        if user_id == admin["id"]:
            raise ValidationError("status", "You cannot change the status of your own account")
        user = await self._store.get_by_id(user_id)
        if user is None:
            raise ValidationError("user_id", "No such account")
        await self._store.update_user(user_id, status=status)
        if status == DISABLED:
            await self._store.delete_sessions_of(user_id)  # a disabled user is logged out at once
        user = await self._store.get_by_id(user_id)
        assert user is not None
        return public_user(user)

    async def _create_account(
        self, full_name: str, email: str, password: str, role: str
    ) -> dict[str, Any]:
        full_name = (full_name or "").strip()
        email = (email or "").strip().lower()
        if not full_name:
            raise ValidationError("full_name", "Name is required")
        if not _EMAIL_RE.match(email):
            raise ValidationError("email", "Enter a valid e-mail address")
        if len(password or "") < self._min_length:
            raise ValidationError(
                "password", f"Password must be at least {self._min_length} characters"
            )
        if await self._store.get_by_email(email) is not None:
            raise ConflictError("This e-mail is already used by another account")
        user = await self._store.create_user(
            full_name, email, hash_password(password), role, self._clock()
        )
        return public_user(user)

    # ------------------------------------------------------------------
    # Login, logout, sessions
    # ------------------------------------------------------------------

    async def login(self, email: str, password: str) -> tuple[str, dict[str, Any]]:
        """Returns (token, user). Raises one general error for a wrong e-mail or password."""
        now = self._clock()
        user = await self._store.get_by_email((email or "").strip().lower())
        if user is None:
            raise AuthenticationError()

        locked_until = _parse(user["locked_until"])
        if locked_until and locked_until > now:
            raise AccountLockedError(self._lock_minutes)

        if not verify_password(password or "", user["password_hash"]):
            await self._record_failure(user, now)
            raise AuthenticationError()

        if user["status"] == DISABLED:
            raise AuthenticationError("This account is disabled")

        await self._store.update_user(
            user["id"], failed_logins=0, first_failed_at=None, locked_until=None
        )
        token = secrets.token_urlsafe(32)
        await self._store.create_session(_token_hash(token), user["id"], now)
        return token, public_user(user)

    async def logout(self, token: str) -> None:
        await self._store.delete_session(_token_hash(token))

    async def user_for_token(self, token: str | None) -> dict[str, Any]:
        """The logged-in user of a token; also renews the idle timer."""
        if not token:
            raise AuthenticationError("Please log in")
        token_hash = _token_hash(token)
        session = await self._store.get_session(token_hash)
        if session is None:
            raise AuthenticationError("Please log in")
        now = self._clock()
        if now - session["last_seen_at"] > self._idle:
            await self._store.delete_session(token_hash)
            raise AuthenticationError("Your session has expired. Please log in again")
        user = await self._store.get_by_id(session["user_id"])
        if user is None or user["status"] == DISABLED:
            await self._store.delete_session(token_hash)
            raise AuthenticationError("Please log in")
        await self._store.touch_session(token_hash, now)
        return public_user(user)

    @staticmethod
    def require_role(user: dict[str, Any], *roles: str) -> None:
        if user["role"] not in roles:
            raise PermissionDeniedError()

    async def _record_failure(self, user: dict[str, Any], now: datetime) -> None:
        first = _parse(user["first_failed_at"])
        if first is None or now - first > self._failed_window:
            count, first = 1, now  # a new 10-minute window starts
        else:
            count = user["failed_logins"] + 1
        if count >= self._max_failed:
            await self._store.update_user(
                user["id"], failed_logins=0, first_failed_at=None, locked_until=now + self._lock
            )
            logger.warning("Account %s locked after %d wrong passwords", user["email"], count)
        else:
            await self._store.update_user(user["id"], failed_logins=count, first_failed_at=first)


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
