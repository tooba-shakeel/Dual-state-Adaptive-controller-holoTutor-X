"""
Evaluation routes for the evaluator screen (SRS 3.2.9, screens 9 and 10).

All routes need an evaluator (or administrator) login. The run happens in the
background; the screen polls GET /api/evaluation/status for its progress.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.routes.auth import current_user, require_roles
from app.config import settings
from app.dependencies import get_evaluation_manager
from app.services.auth_service import ADMINISTRATOR, EVALUATOR
from app.services.evaluation import (
    DEFAULT_VERSIONS,
    EvaluationBusyError,
    EvaluationManager,
    QuestionFileError,
)

router = APIRouter(
    prefix="/api/evaluation",
    tags=["evaluation"],
    dependencies=[Depends(require_roles(EVALUATOR, ADMINISTRATOR))],
)


class RunRequest(BaseModel):
    versions: list[str] = Field(default_factory=lambda: list(DEFAULT_VERSIONS))
    delay: float = Field(default_factory=lambda: settings.EVALUATION_DELAY_SECONDS, ge=0, le=120)
    document_ids: list[str] | None = None
    fresh: bool = False  # True: discard earlier results; False: continue where it stopped


@router.post("/questions")
async def upload_questions(
    file: UploadFile = File(...),
    manager: EvaluationManager = Depends(get_evaluation_manager),
) -> dict[str, Any]:
    """Upload the test question CSV (id, question, answerable, reference_page, learner_state)."""
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="The question file must be a UTF-8 CSV")
    try:
        return manager.save_questions(text)
    except QuestionFileError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except EvaluationBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/questions")
async def question_info(manager: EvaluationManager = Depends(get_evaluation_manager)) -> dict[str, Any]:
    info = manager.question_info()
    if info is None:
        raise HTTPException(status_code=404, detail="No question file uploaded yet")
    return info


@router.post("/run", status_code=202)
async def start_run(
    body: RunRequest,
    user: dict[str, Any] = Depends(current_user),
    manager: EvaluationManager = Depends(get_evaluation_manager),
) -> dict[str, Any]:
    """Run over the evaluator's own uploaded documents."""
    try:
        return await manager.start(
            body.versions, body.delay, body.document_ids, body.fresh, owner_id=user["id"]
        )
    except EvaluationBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (ValueError, QuestionFileError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/stop")
async def stop_run(manager: EvaluationManager = Depends(get_evaluation_manager)) -> dict[str, Any]:
    return manager.stop()


@router.get("/status")
async def run_status(manager: EvaluationManager = Depends(get_evaluation_manager)) -> dict[str, Any]:
    return manager.status()


@router.get("/results")
async def results(manager: EvaluationManager = Depends(get_evaluation_manager)) -> dict[str, Any]:
    """The side-by-side comparison of the versions."""
    return {"summary": manager.summary()}


@router.get("/download/{kind}")
async def download(
    kind: Literal["results", "summary"],
    manager: EvaluationManager = Depends(get_evaluation_manager),
) -> FileResponse:
    path = manager.export_summary() if kind == "summary" else manager.results_path
    if not path.exists():
        raise HTTPException(status_code=404, detail="No results yet")
    return FileResponse(path, media_type="text/csv", filename=path.name)
