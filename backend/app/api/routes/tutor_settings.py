"""
Tutor Settings routes (administrator only).

GET  /api/admin/tutor-settings        current limits, decision table, version and history
PUT  /api/admin/tutor-settings        save new limits and table (next version)
POST /api/admin/tutor-settings/reset  go back to the default limits and table (next version)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.routes.auth import require_roles
from app.dependencies import get_tutor_settings
from app.services.auth_service import ADMINISTRATOR
from app.services.tutor_settings import TutorSettingsError, TutorSettingsService

router = APIRouter(prefix="/api/admin/tutor-settings", tags=["tutor settings"])


class SettingsRequest(BaseModel):
    partial_limit: float = Field(ge=0, le=1)
    strong_limit: float = Field(ge=0, le=1)
    cells: dict[str, dict[str, str]]


@router.get("")
async def get_settings(
    _admin: dict[str, Any] = Depends(require_roles(ADMINISTRATOR)),
    service: TutorSettingsService = Depends(get_tutor_settings),
) -> dict[str, Any]:
    return service.describe()


@router.put("")
async def save_settings(
    body: SettingsRequest,
    admin: dict[str, Any] = Depends(require_roles(ADMINISTRATOR)),
    service: TutorSettingsService = Depends(get_tutor_settings),
) -> dict[str, Any]:
    try:
        return service.update(body.partial_limit, body.strong_limit, body.cells, admin["email"])
    except TutorSettingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/reset")
async def reset_settings(
    admin: dict[str, Any] = Depends(require_roles(ADMINISTRATOR)),
    service: TutorSettingsService = Depends(get_tutor_settings),
) -> dict[str, Any]:
    return service.reset(admin["email"])
