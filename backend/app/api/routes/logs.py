"""
Interaction-log routes for the administrator (SRS LOG module; Logs and Unanswered screens).

GET /api/admin/logs        every logged question, newest first (filters: user, dates)
GET /api/admin/unanswered  only the questions the tutor could not answer

Administrator only.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.routes.auth import require_roles
from app.dependencies import get_interaction_log
from app.services.auth_service import ADMINISTRATOR
from app.storage.interaction_log_store import SQLiteInteractionLogStore

router = APIRouter(
    prefix="/api/admin",
    tags=["logs"],
    dependencies=[Depends(require_roles(ADMINISTRATOR))],
)

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _check_date(name: str, value: str | None) -> None:
    if value is not None and not _DATE.match(value):
        raise HTTPException(status_code=422, detail=f"{name} must be a date like 2026-11-15")


async def _page(
    log: SQLiteInteractionLogStore,
    *,
    unanswered_only: bool,
    limit: int,
    offset: int,
    user_id: int | None,
    date_from: str | None,
    date_to: str | None,
) -> dict[str, Any]:
    _check_date("date_from", date_from)
    _check_date("date_to", date_to)
    items, total = await log.list_entries(
        limit=limit,
        offset=offset,
        user_id=user_id,
        unanswered_only=unanswered_only,
        date_from=date_from,
        date_to=date_to,
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/logs")
async def list_logs(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    log: SQLiteInteractionLogStore = Depends(get_interaction_log),
) -> dict[str, Any]:
    return await _page(
        log, unanswered_only=False, limit=limit, offset=offset,
        user_id=user_id, date_from=date_from, date_to=date_to,
    )


@router.get("/unanswered")
async def list_unanswered(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    log: SQLiteInteractionLogStore = Depends(get_interaction_log),
) -> dict[str, Any]:
    return await _page(
        log, unanswered_only=True, limit=limit, offset=offset,
        user_id=user_id, date_from=date_from, date_to=date_to,
    )
