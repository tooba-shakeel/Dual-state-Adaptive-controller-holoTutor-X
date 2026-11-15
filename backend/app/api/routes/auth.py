"""
Account routes (SRS 3.2.1, UAM).

Student:        POST /api/auth/signup, POST /api/auth/login, POST /api/auth/logout, GET /api/auth/me
Administrator:  GET /api/admin/users, POST /api/admin/evaluators, PATCH /api/admin/users/{id}/status

The login token is sent as "Authorization: Bearer <token>".
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel

from app.dependencies import get_auth_service
from app.services.auth_service import ADMINISTRATOR, AuthService

router = APIRouter(prefix="/api", tags=["accounts"])


# ── Request bodies ───────────────────────────────────────────────────

class SignUpRequest(BaseModel):
    full_name: str
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class StatusRequest(BaseModel):
    status: Literal["active", "disabled"]


# ── Who is calling ───────────────────────────────────────────────────

def _bearer_token(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


async def current_user(
    authorization: str | None = Header(default=None),
    auth: AuthService = Depends(get_auth_service),
) -> dict[str, Any]:
    """The logged-in user; 401 if the token is missing, wrong or expired."""
    return await auth.user_for_token(_bearer_token(authorization))


def require_roles(*roles: str):
    """Dependency that also checks the user's role; 403 for any other role."""

    async def checker(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        AuthService.require_role(user, *roles)
        return user

    return checker


# ── Student and everyone ─────────────────────────────────────────────

@router.post("/auth/signup", status_code=201)
async def sign_up(body: SignUpRequest, auth: AuthService = Depends(get_auth_service)) -> dict[str, Any]:
    return await auth.sign_up(body.full_name, body.email, body.password)


@router.post("/auth/login")
async def login(body: LoginRequest, auth: AuthService = Depends(get_auth_service)) -> dict[str, Any]:
    token, user = await auth.login(body.email, body.password)
    return {"token": token, "user": user}


@router.post("/auth/logout", status_code=204)
async def logout(
    authorization: str | None = Header(default=None),
    auth: AuthService = Depends(get_auth_service),
) -> None:
    token = _bearer_token(authorization)
    if token:
        await auth.logout(token)


@router.get("/auth/me")
async def me(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return user


# ── Administrator ────────────────────────────────────────────────────

@router.get("/admin/users")
async def list_users(
    admin: dict[str, Any] = Depends(require_roles(ADMINISTRATOR)),
    auth: AuthService = Depends(get_auth_service),
) -> list[dict[str, Any]]:
    return await auth.list_users(admin)


@router.post("/admin/evaluators", status_code=201)
async def create_evaluator(
    body: SignUpRequest,
    admin: dict[str, Any] = Depends(require_roles(ADMINISTRATOR)),
    auth: AuthService = Depends(get_auth_service),
) -> dict[str, Any]:
    return await auth.create_evaluator(admin, body.full_name, body.email, body.password)


@router.patch("/admin/users/{user_id}/status")
async def set_status(
    user_id: int,
    body: StatusRequest,
    admin: dict[str, Any] = Depends(require_roles(ADMINISTRATOR)),
    auth: AuthService = Depends(get_auth_service),
) -> dict[str, Any]:
    return await auth.set_status(admin, user_id, body.status)
