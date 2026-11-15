"""
Tests for User Account Management (SRS 3.2.1).
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.exceptions import (
    AccountLockedError,
    AuthenticationError,
    ConflictError,
    PermissionDeniedError,
    ValidationError,
)
from app.services.auth_service import AuthService, hash_password, verify_password
from app.storage.user_store import SQLiteUserStore


class Clock:
    """A clock the test can move forward."""

    def __init__(self):
        self.now = datetime(2026, 11, 15, 10, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, minutes):
        self.now += timedelta(minutes=minutes)


@pytest_asyncio.fixture
async def clock():
    return Clock()


@pytest_asyncio.fixture
async def auth(tmp_path, clock):
    store = SQLiteUserStore(str(tmp_path / "users.db"))
    await store.initialize()
    service = AuthService(store, clock=clock)
    await service.ensure_administrator("Admin", "admin@ned.edu", "admin-pass-1")
    return service


async def admin_of(auth):
    _, admin = await auth.login("admin@ned.edu", "admin-pass-1")
    return admin


# ── Passwords ────────────────────────────────────────────────────────

def test_password_is_hashed_and_checked():
    stored = hash_password("secret-123")
    assert "secret-123" not in stored
    assert verify_password("secret-123", stored)
    assert not verify_password("secret-124", stored)


# ── Sign up ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sign_up_creates_a_student(auth):
    user = await auth.sign_up("Tooba", "Tooba@NED.edu ", "password1")
    assert user["role"] == "student"
    assert user["email"] == "tooba@ned.edu"
    assert "password_hash" not in user


@pytest.mark.asyncio
async def test_password_shorter_than_eight_is_rejected(auth):
    with pytest.raises(ValidationError):
        await auth.sign_up("Tooba", "tooba@ned.edu", "1234567")
    assert (await auth.sign_up("Tooba", "tooba@ned.edu", "12345678"))["role"] == "student"


@pytest.mark.asyncio
async def test_email_used_twice_is_rejected(auth):
    await auth.sign_up("Tooba", "tooba@ned.edu", "password1")
    with pytest.raises(ConflictError):
        await auth.sign_up("Someone", "TOOBA@ned.edu", "password2")


@pytest.mark.asyncio
async def test_invalid_email_is_rejected(auth):
    with pytest.raises(ValidationError):
        await auth.sign_up("Tooba", "not-an-email", "password1")


# ── Login, lock, logout, idle timeout ────────────────────────────────

@pytest.mark.asyncio
async def test_login_returns_token_that_identifies_the_user(auth):
    await auth.sign_up("Tooba", "tooba@ned.edu", "password1")
    token, user = await auth.login("tooba@ned.edu", "password1")
    assert (await auth.user_for_token(token))["id"] == user["id"]


@pytest.mark.asyncio
async def test_wrong_email_and_wrong_password_give_the_same_message(auth):
    await auth.sign_up("Tooba", "tooba@ned.edu", "password1")
    with pytest.raises(AuthenticationError) as wrong_password:
        await auth.login("tooba@ned.edu", "wrong-pass")
    with pytest.raises(AuthenticationError) as wrong_email:
        await auth.login("nobody@ned.edu", "password1")
    assert wrong_password.value.message == wrong_email.value.message


@pytest.mark.asyncio
async def test_five_wrong_tries_lock_the_account_for_five_minutes(auth, clock):
    await auth.sign_up("Tooba", "tooba@ned.edu", "password1")
    for _ in range(5):
        with pytest.raises(AuthenticationError):
            await auth.login("tooba@ned.edu", "wrong-pass")
    with pytest.raises(AccountLockedError):
        await auth.login("tooba@ned.edu", "password1")  # even the right password
    clock.advance(5.1)
    token, _ = await auth.login("tooba@ned.edu", "password1")
    assert token


@pytest.mark.asyncio
async def test_wrong_tries_spread_over_more_than_ten_minutes_do_not_lock(auth, clock):
    await auth.sign_up("Tooba", "tooba@ned.edu", "password1")
    for _ in range(4):
        with pytest.raises(AuthenticationError):
            await auth.login("tooba@ned.edu", "wrong-pass")
    clock.advance(11)
    with pytest.raises(AuthenticationError):
        await auth.login("tooba@ned.edu", "wrong-pass")  # starts a new window
    token, _ = await auth.login("tooba@ned.edu", "password1")
    assert token


@pytest.mark.asyncio
async def test_token_expires_after_thirty_idle_minutes(auth, clock):
    await auth.sign_up("Tooba", "tooba@ned.edu", "password1")
    token, _ = await auth.login("tooba@ned.edu", "password1")
    clock.advance(29)
    await auth.user_for_token(token)  # activity renews the timer
    clock.advance(29)
    await auth.user_for_token(token)
    clock.advance(31)
    with pytest.raises(AuthenticationError):
        await auth.user_for_token(token)


@pytest.mark.asyncio
async def test_logout_stops_the_token_at_once(auth):
    await auth.sign_up("Tooba", "tooba@ned.edu", "password1")
    token, _ = await auth.login("tooba@ned.edu", "password1")
    await auth.logout(token)
    with pytest.raises(AuthenticationError):
        await auth.user_for_token(token)


# ── Roles and account status ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_administrator_is_created_once(auth):
    await auth.ensure_administrator("Admin 2", "other@ned.edu", "admin-pass-2")
    users = await auth.list_users(await admin_of(auth))
    assert [u["email"] for u in users if u["role"] == "administrator"] == ["admin@ned.edu"]


@pytest.mark.asyncio
async def test_only_the_administrator_creates_evaluators(auth):
    student = await auth.sign_up("Tooba", "tooba@ned.edu", "password1")
    with pytest.raises(PermissionDeniedError):
        await auth.create_evaluator(student, "Eva", "eva@ned.edu", "password1")
    evaluator = await auth.create_evaluator(await admin_of(auth), "Eva", "eva@ned.edu", "password1")
    assert evaluator["role"] == "evaluator"


@pytest.mark.asyncio
async def test_disabled_account_cannot_log_in_and_is_logged_out(auth):
    student = await auth.sign_up("Tooba", "tooba@ned.edu", "password1")
    token, _ = await auth.login("tooba@ned.edu", "password1")
    await auth.set_status(await admin_of(auth), student["id"], "disabled")
    with pytest.raises(AuthenticationError):
        await auth.user_for_token(token)
    with pytest.raises(AuthenticationError):
        await auth.login("tooba@ned.edu", "password1")
    await auth.set_status(await admin_of(auth), student["id"], "active")
    assert (await auth.login("tooba@ned.edu", "password1"))[0]


@pytest.mark.asyncio
async def test_administrator_cannot_disable_own_account(auth):
    admin = await admin_of(auth)
    with pytest.raises(ValidationError):
        await auth.set_status(admin, admin["id"], "disabled")


# ── HTTP routes ──────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    from app.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "ADMIN_EMAIL", "admin@ned.edu")
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "admin-pass-1")
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_routes_sign_up_login_me_logout(client):
    r = await client.post("/api/auth/signup", json={"full_name": "Tooba", "email": "t@ned.edu", "password": "password1"})
    assert r.status_code == 201 and r.json()["role"] == "student"
    assert (await client.post("/api/auth/signup", json={"full_name": "X", "email": "t@ned.edu", "password": "password1"})).status_code == 409
    assert (await client.post("/api/auth/signup", json={"full_name": "X", "email": "x@ned.edu", "password": "short"})).status_code == 422

    assert (await client.post("/api/auth/login", json={"email": "t@ned.edu", "password": "nope-nope"})).status_code == 401
    token = (await client.post("/api/auth/login", json={"email": "t@ned.edu", "password": "password1"})).json()["token"]
    assert (await client.get("/api/auth/me", headers=bearer(token))).json()["email"] == "t@ned.edu"

    assert (await client.post("/api/auth/logout", headers=bearer(token))).status_code == 204
    assert (await client.get("/api/auth/me", headers=bearer(token))).status_code == 401


@pytest.mark.asyncio
async def test_admin_routes_need_the_administrator_role(client):
    await client.post("/api/auth/signup", json={"full_name": "Tooba", "email": "t@ned.edu", "password": "password1"})
    student = (await client.post("/api/auth/login", json={"email": "t@ned.edu", "password": "password1"})).json()["token"]
    admin = (await client.post("/api/auth/login", json={"email": "admin@ned.edu", "password": "admin-pass-1"})).json()["token"]

    assert (await client.get("/api/admin/users")).status_code == 401
    assert (await client.get("/api/admin/users", headers=bearer(student))).status_code == 403

    r = await client.post("/api/admin/evaluators", headers=bearer(admin),
                          json={"full_name": "Eva", "email": "eva@ned.edu", "password": "password1"})
    assert r.status_code == 201 and r.json()["role"] == "evaluator"

    users = (await client.get("/api/admin/users", headers=bearer(admin))).json()
    student_id = next(u["id"] for u in users if u["email"] == "t@ned.edu")
    r = await client.patch(f"/api/admin/users/{student_id}/status", headers=bearer(admin), json={"status": "disabled"})
    assert r.json()["status"] == "disabled"
    assert (await client.get("/api/auth/me", headers=bearer(student))).status_code == 401
