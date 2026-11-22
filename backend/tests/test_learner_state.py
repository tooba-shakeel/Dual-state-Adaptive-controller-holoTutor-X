"""Unit tests for the LSE (SRS 3.2.4). No Gemini or network needed."""

import asyncio

import pytest

from app.services.learner_state import (
    CONFUSED,
    REPEATED_FAILURE,
    UNCERTAIN,
    UNDERSTANDING,
    LearnerStateEstimator,
    classify_signals,
)
from app.storage.signal_store import SQLiteSignalStore


# ── Pure rule: signals are given newest first ────────────────────────────

def test_no_signals_is_uncertain():
    assert classify_signals([]) == UNCERTAIN


def test_three_confused_is_repeated_failure():
    assert classify_signals([-1, -1, -1]) == REPEATED_FAILURE


def test_latest_confused_is_confused():
    assert classify_signals([-1, 1, 1]) == CONFUSED


def test_two_confused_only_is_confused_not_repeated_failure():
    assert classify_signals([-1, -1]) == CONFUSED


def test_latest_got_it_without_confused_is_understanding():
    assert classify_signals([1, 0, 1]) == UNDERSTANDING
    assert classify_signals([1]) == UNDERSTANDING


def test_latest_got_it_but_earlier_confused_is_uncertain():
    assert classify_signals([1, -1, 1]) == UNCERTAIN


def test_latest_not_sure_is_uncertain():
    assert classify_signals([0, 1, 1]) == UNCERTAIN
    assert classify_signals([0]) == UNCERTAIN


def test_only_last_three_signals_count():
    # newest first: the 4th signal (-1) is ignored
    assert classify_signals([1, 1, 1, -1]) == UNDERSTANDING


# ── Estimator with a real temporary SQLite file ──────────────────────────

def _make_estimator(tmp_path):
    return LearnerStateEstimator(SQLiteSignalStore(str(tmp_path / "test.db")))


def test_new_conversation_is_uncertain(tmp_path):
    est = _make_estimator(tmp_path)
    assert asyncio.run(est.estimate("c1")) == UNCERTAIN


def test_estimator_walks_through_all_four_states(tmp_path):
    est = _make_estimator(tmp_path)

    async def scenario():
        out = {}
        await est.add_signal("c1", 1, "doc:0")
        out["understanding"] = await est.estimate("c1", "doc:0")
        await est.add_signal("c1", 0, "doc:0")
        out["uncertain"] = await est.estimate("c1", "doc:0")
        await est.add_signal("c1", -1, "doc:0")
        out["confused"] = await est.estimate("c1", "doc:0")
        await est.add_signal("c1", -1, "doc:0")
        await est.add_signal("c1", -1, "doc:0")
        out["repeated"] = await est.estimate("c1", "doc:0")
        return out

    out = asyncio.run(scenario())
    assert out == {
        "understanding": UNDERSTANDING,
        "uncertain": UNCERTAIN,
        "confused": CONFUSED,
        "repeated": REPEATED_FAILURE,
    }


def test_new_topic_starts_uncertain(tmp_path):
    est = _make_estimator(tmp_path)

    async def scenario():
        for _ in range(3):
            await est.add_signal("c1", -1, "doc:0")
        old = await est.estimate("c1", "doc:0")
        new = await est.estimate("c1", "doc:1")
        return old, new

    old, new = asyncio.run(scenario())
    assert old == REPEATED_FAILURE
    assert new == UNCERTAIN


def test_conversations_are_independent(tmp_path):
    est = _make_estimator(tmp_path)

    async def scenario():
        await est.add_signal("c1", -1)
        return await est.estimate("c1"), await est.estimate("c2")

    c1, c2 = asyncio.run(scenario())
    assert c1 == CONFUSED
    assert c2 == UNCERTAIN


def test_estimate_without_topic_uses_whole_conversation(tmp_path):
    est = _make_estimator(tmp_path)

    async def scenario():
        await est.add_signal("c1", 1, "doc:0")
        await est.add_signal("c1", -1, "doc:1")
        return await est.estimate("c1")

    assert asyncio.run(scenario()) == CONFUSED


def test_invalid_signal_is_rejected(tmp_path):
    est = _make_estimator(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(est.add_signal("c1", 2))
