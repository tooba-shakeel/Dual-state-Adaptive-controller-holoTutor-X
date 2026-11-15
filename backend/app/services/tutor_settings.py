"""
Tutor Settings (SRS 3.2.5, 3.2.6; administrator screen).

The administrator can change, while the app runs:
  - the evidence limits used by ECA (partial limit, strong limit), and
  - the 4 x 4 decision table used by DSC.

Every saved change gets the next table version number, so the interaction log
and the Tutor State panel show which version made each decision. The current
settings and a short history are kept in ``<DATA_DIR>/tutor_settings.json``;
the decision table in the code (``app/data/decision_table.json``) stays the default.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.tutor_models import LEARNER_STATES, EvidenceState, TeachingAction
from app.services.decision_controller import DecisionController, DecisionTableError, validate_cells
from app.services.evidence_assessor import EvidenceAssessor

logger = logging.getLogger(__name__)

HISTORY_SIZE = 20


class TutorSettingsError(ValueError):
    """The new settings break an SRS rule; nothing was changed."""


class TutorSettingsService:
    def __init__(
        self,
        path: str | Path,
        assessor: EvidenceAssessor,
        controller: DecisionController,
        default_limits: tuple[float, float],
        default_cells: dict[str, dict[str, str]],
    ) -> None:
        self._path = Path(path)
        self._assessor = assessor
        self._controller = controller
        self._default_limits = default_limits
        self._default_cells = {k: dict(v) for k, v in default_cells.items()}
        self._updated_at: str | None = None
        self._updated_by: str | None = None
        self._history: list[dict[str, Any]] = []

    # ── startup ─────────────────────────────────────────────────────

    def load(self) -> None:
        """Apply saved settings, if any. A broken file is ignored and the defaults stay."""
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            current = data["current"]
            self._apply(current["partial_limit"], current["strong_limit"], current["cells"], current["version"])
            self._updated_at = current.get("updated_at")
            self._updated_by = current.get("updated_by")
            self._history = list(data.get("history", []))[:HISTORY_SIZE]
            logger.info("Loaded tutor settings version %s from %s", current["version"], self._path)
        except Exception:  # noqa: BLE001
            logger.exception("Could not read %s; using the default tutor settings", self._path)

    # ── read ────────────────────────────────────────────────────────

    def current(self) -> dict[str, Any]:
        return {
            "version": self._controller.version,
            "partial_limit": self._assessor.partial_limit,
            "strong_limit": self._assessor.strong_limit,
            "cells": self._controller.cells,
            "updated_at": self._updated_at,
            "updated_by": self._updated_by,
        }

    def describe(self) -> dict[str, Any]:
        """Current settings plus what the screen needs to draw the table."""
        return {
            **self.current(),
            "learner_states": list(LEARNER_STATES),
            "evidence_states": [e.value for e in EvidenceState],
            "actions": [a.value for a in TeachingAction],
            "locked": {"insufficient": TeachingAction.STATE_UNCERTAINTY.value},
            "defaults": {
                "partial_limit": self._default_limits[0],
                "strong_limit": self._default_limits[1],
                "cells": self._default_cells,
            },
            "history": self._history,
        }

    # ── change ──────────────────────────────────────────────────────

    def update(
        self,
        partial_limit: float,
        strong_limit: float,
        cells: dict[str, dict[str, str]],
        changed_by: str,
    ) -> dict[str, Any]:
        """Check everything first, then apply and save as the next version."""
        try:
            EvidenceAssessor.check_limits(partial_limit, strong_limit)
            checked = validate_cells(cells)
        except (ValueError, DecisionTableError) as exc:
            raise TutorSettingsError(str(exc)) from None
        clean = {learner: {ev: a.value for ev, a in row.items()} for learner, row in checked.items()}

        before = self.current()
        if (
            before["partial_limit"] == float(partial_limit)
            and before["strong_limit"] == float(strong_limit)
            and before["cells"] == clean
        ):
            return self.describe()  # nothing changed: keep the same version

        self._history.insert(0, before)
        del self._history[HISTORY_SIZE:]
        self._apply(partial_limit, strong_limit, clean, before["version"] + 1)
        self._updated_at = datetime.now(timezone.utc).isoformat()
        self._updated_by = changed_by
        self._save()
        logger.info("Tutor settings changed to version %d by %s", self._controller.version, changed_by)
        return self.describe()

    def reset(self, changed_by: str) -> dict[str, Any]:
        partial, strong = self._default_limits
        return self.update(partial, strong, self._default_cells, changed_by)

    # ── helpers ─────────────────────────────────────────────────────

    def _apply(self, partial: float, strong: float, cells: dict[str, dict[str, str]], version: int) -> None:
        # Check both before changing either, so a bad file never leaves half-applied settings.
        EvidenceAssessor.check_limits(partial, strong)
        validate_cells(cells)
        self._assessor.set_limits(partial, strong)
        self._controller.set_table(cells, version)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"current": self.current(), "history": self._history}, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self._path)  # the old file stays whole if writing fails
