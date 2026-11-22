"""
Stand-in for the Learner State Estimation (LSE) module.

Every topic is Uncertain, which is the SRS start state for a new topic. At
integration this is replaced by the real LSE estimator, which has the same
two methods.
"""

from __future__ import annotations

from app.core.tutor_models import DEFAULT_LEARNER_STATE


class FixedLearnerStateEstimator:
    """Always reports the default learner state and ignores signals."""

    async def add_signal(self, conversation_id: str, signal: int, topic: str | None = None) -> None:
        return None

    async def estimate(self, conversation_id: str, topic: str | None = None) -> str:
        return DEFAULT_LEARNER_STATE
