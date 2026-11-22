"""
Learner State Estimation (LSE) — SRS 3.2.4.

Turns the student's last rating signals into one of four learner states.
Signals: Got it = +1, Not sure = 0, Confused = -1.

Rule (looks at the last 3 signals of the topic, newest first):
    1. three signals, all -1        -> repeated_failure
    2. latest signal is -1          -> confused
    3. latest is +1, no -1 present  -> understanding
    4. anything else / no signals   -> uncertain   (a new topic is uncertain)

The returned values are plain strings equal to the ``LearnerState`` enum values
in the shared contract, so they can be compared with or converted to the enum.
"""

from __future__ import annotations

from app.storage.signal_store import SQLiteSignalStore

UNDERSTANDING = "understanding"
UNCERTAIN = "uncertain"
CONFUSED = "confused"
REPEATED_FAILURE = "repeated_failure"

_WINDOW = 3
_VALID_SIGNALS = (-1, 0, 1)


def classify_signals(signals_newest_first: list[int]) -> str:
    """Pure function: map up to the last 3 signals (newest first) to a state."""
    recent = signals_newest_first[:_WINDOW]
    if not recent:
        return UNCERTAIN
    if len(recent) == _WINDOW and all(s == -1 for s in recent):
        return REPEATED_FAILURE
    if recent[0] == -1:
        return CONFUSED
    if recent[0] == 1 and -1 not in recent:
        return UNDERSTANDING
    return UNCERTAIN


class LearnerStateEstimator:
    """LSE module. Same interface the chat service expects."""

    def __init__(self, store: SQLiteSignalStore) -> None:
        self._store = store

    async def add_signal(
        self, conversation_id: str, signal: int, topic: str | None = None
    ) -> None:
        if signal not in _VALID_SIGNALS:
            raise ValueError(f"signal must be -1, 0 or 1, got {signal!r}")
        await self._store.add_signal(conversation_id, signal, topic)

    async def estimate(
        self, conversation_id: str, topic: str | None = None
    ) -> str:
        signals = await self._store.get_recent_signals(
            conversation_id, topic, limit=_WINDOW
        )
        return classify_signals(signals)
