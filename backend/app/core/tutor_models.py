"""
Dual-State tutoring models — evidence confidence, teaching actions and the
Tutor State shown to the student.

Learner states belong to the Learner State Estimation (LSE) module. They are
kept here only as plain string values so this module does not depend on it.
"""

from __future__ import annotations

import enum

from pydantic import BaseModel

# Values the LSE module returns (SRS, Appendix A.2).
LEARNER_STATES: tuple[str, ...] = (
    "understanding",
    "uncertain",
    "confused",
    "repeated_failure",
)

# State of a topic that has no signals yet (SRS 3.2.4).
DEFAULT_LEARNER_STATE = "uncertain"


class EvidenceState(str, enum.Enum):
    """How strongly the retrieved passages support an answer (SRS 3.2.5)."""

    STRONG = "strong"
    PARTIAL = "partial"
    CONFLICTING = "conflicting"
    INSUFFICIENT = "insufficient"


class TeachingAction(str, enum.Enum):
    """Response method chosen by the Dual-State Controller (SRS 3.2.6)."""

    SIMPLIFY = "simplify"
    WORKED_EXAMPLE = "worked_example"
    ADVANCE = "advance"
    CLARIFY = "clarify"
    STATE_UNCERTAINTY = "state_uncertainty"


class TutorState(BaseModel):
    """What the Tutor State panel shows for one answer."""

    learner_state: str
    evidence_state: EvidenceState
    evidence_score: float  # best similarity score, 0 to 1
    teaching_action: TeachingAction
    table_version: int
