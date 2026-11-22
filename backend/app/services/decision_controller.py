"""
Dual-State Controller (DSC) — picks the teaching action from the pair
(learner state, evidence confidence) using a 4 x 4 decision table (SRS 3.2.6).

The table lives in ``app/data/decision_table.json`` so it can be changed
without touching code. A table is rejected if a cell is missing or if the
Insufficient column holds anything other than State uncertainty.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.core.tutor_models import (
    DEFAULT_LEARNER_STATE,
    LEARNER_STATES,
    EvidenceState,
    TeachingAction,
)

logger = logging.getLogger(__name__)

# Default table — SRS Table 3.1.
DEFAULT_CELLS: dict[str, dict[str, str]] = {
    "understanding": {
        "strong": "advance",
        "partial": "clarify",
        "conflicting": "clarify",
        "insufficient": "state_uncertainty",
    },
    "uncertain": {
        "strong": "worked_example",
        "partial": "simplify",
        "conflicting": "clarify",
        "insufficient": "state_uncertainty",
    },
    "confused": {
        "strong": "simplify",
        "partial": "simplify",
        "conflicting": "clarify",
        "insufficient": "state_uncertainty",
    },
    "repeated_failure": {
        "strong": "worked_example",
        "partial": "worked_example",
        "conflicting": "state_uncertainty",
        "insufficient": "state_uncertainty",
    },
}


class DecisionTableError(ValueError):
    """Raised when a decision table breaks the SRS rules."""


def validate_cells(cells: dict[str, dict[str, str]]) -> dict[str, dict[str, TeachingAction]]:
    """Check every one of the 16 cells and return them as ``TeachingAction`` values."""
    checked: dict[str, dict[str, TeachingAction]] = {}
    for learner in LEARNER_STATES:
        row = cells.get(learner)
        if not isinstance(row, dict):
            raise DecisionTableError(f"Row '{learner}' is missing")
        checked[learner] = {}
        for evidence in EvidenceState:
            value = row.get(evidence.value)
            if not value:
                raise DecisionTableError(f"Cell ({learner}, {evidence.value}) is empty")
            try:
                action = TeachingAction(value)
            except ValueError:
                raise DecisionTableError(
                    f"Cell ({learner}, {evidence.value}) has unknown action '{value}'"
                ) from None
            if evidence is EvidenceState.INSUFFICIENT and action is not TeachingAction.STATE_UNCERTAINTY:
                raise DecisionTableError(
                    f"Cell ({learner}, insufficient) must be state_uncertainty"
                )
            checked[learner][evidence.value] = action
    return checked


class DecisionController:
    """Chooses one teaching action per answer."""

    def __init__(self, cells: dict[str, dict[str, str]] | None = None, version: int = 1) -> None:
        self._cells = validate_cells(cells if cells is not None else DEFAULT_CELLS)
        self._version = version
        # Last action per conversation, for the "no Clarify twice in a row" rule.
        self._last_action: dict[str, TeachingAction] = {}

    @classmethod
    def from_file(cls, path: str | Path) -> DecisionController:
        """Load the table from JSON; fall back to the SRS default if the file is missing."""
        path = Path(path)
        if not path.exists():
            logger.warning("Decision table %s not found; using the default table", path)
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        controller = cls(cells=data.get("cells"), version=int(data.get("version", 1)))
        logger.info("Loaded decision table version %d from %s", controller.version, path)
        return controller

    @property
    def version(self) -> int:
        return self._version

    @property
    def cells(self) -> dict[str, dict[str, str]]:
        """The table as plain strings (learner state -> evidence state -> action)."""
        return {learner: {ev: a.value for ev, a in row.items()} for learner, row in self._cells.items()}

    def set_table(self, cells: dict[str, dict[str, str]], version: int) -> None:
        """Replace the table while the app runs (Tutor Settings screen). Invalid tables are rejected."""
        self._cells = validate_cells(cells)
        self._version = int(version)

    def decide(
        self,
        learner_state: str,
        evidence_state: EvidenceState,
        conversation_id: str | None = None,
    ) -> TeachingAction:
        """Look up the action for the pair, then apply the Clarify-twice rule."""
        learner = self._normalise_learner(learner_state)
        action = self._cells[learner][EvidenceState(evidence_state).value]

        if (
            action is TeachingAction.CLARIFY
            and conversation_id is not None
            and self._last_action.get(conversation_id) is TeachingAction.CLARIFY
        ):
            action = TeachingAction.STATE_UNCERTAINTY

        if conversation_id is not None:
            self._last_action[conversation_id] = action
        return action

    @staticmethod
    def _normalise_learner(learner_state: object) -> str:
        """Accept a plain string or an enum from the LSE module."""
        value = str(getattr(learner_state, "value", learner_state)).lower()
        if value not in LEARNER_STATES:
            logger.warning("Unknown learner state %r; using %s", learner_state, DEFAULT_LEARNER_STATE)
            return DEFAULT_LEARNER_STATE
        return value
