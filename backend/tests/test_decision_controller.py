import copy
import json
from pathlib import Path

import pytest

from app.core.tutor_models import EvidenceState as E
from app.core.tutor_models import TeachingAction as A
from app.services.decision_controller import (
    DEFAULT_CELLS,
    DecisionController,
    DecisionTableError,
)

# SRS Table 3.1, cell by cell.
EXPECTED = {
    ("understanding", E.STRONG): A.ADVANCE,
    ("understanding", E.PARTIAL): A.CLARIFY,
    ("understanding", E.CONFLICTING): A.CLARIFY,
    ("understanding", E.INSUFFICIENT): A.STATE_UNCERTAINTY,
    ("uncertain", E.STRONG): A.WORKED_EXAMPLE,
    ("uncertain", E.PARTIAL): A.SIMPLIFY,
    ("uncertain", E.CONFLICTING): A.CLARIFY,
    ("uncertain", E.INSUFFICIENT): A.STATE_UNCERTAINTY,
    ("confused", E.STRONG): A.SIMPLIFY,
    ("confused", E.PARTIAL): A.SIMPLIFY,
    ("confused", E.CONFLICTING): A.CLARIFY,
    ("confused", E.INSUFFICIENT): A.STATE_UNCERTAINTY,
    ("repeated_failure", E.STRONG): A.WORKED_EXAMPLE,
    ("repeated_failure", E.PARTIAL): A.WORKED_EXAMPLE,
    ("repeated_failure", E.CONFLICTING): A.STATE_UNCERTAINTY,
    ("repeated_failure", E.INSUFFICIENT): A.STATE_UNCERTAINTY,
}


@pytest.mark.parametrize("pair,action", EXPECTED.items())
def test_default_table_matches_srs(pair, action):
    assert DecisionController().decide(*pair) is action


def test_json_file_matches_default_table():
    path = Path(__file__).resolve().parent.parent / "app" / "data" / "decision_table.json"
    controller = DecisionController.from_file(path)
    assert controller.version == 1
    for pair, action in EXPECTED.items():
        assert controller.decide(*pair) is action


def test_clarify_twice_in_a_row_becomes_state_uncertainty():
    controller = DecisionController()
    assert controller.decide("understanding", E.PARTIAL, conversation_id="c1") is A.CLARIFY
    assert controller.decide("understanding", E.PARTIAL, conversation_id="c1") is A.STATE_UNCERTAINTY
    # Another conversation is not affected.
    assert controller.decide("understanding", E.PARTIAL, conversation_id="c2") is A.CLARIFY


def test_learner_state_enum_from_lse_is_accepted():
    class LearnerState:
        value = "confused"

    assert DecisionController().decide(LearnerState(), E.STRONG) is A.SIMPLIFY


def test_unknown_learner_state_falls_back_to_uncertain():
    assert DecisionController().decide("sleepy", E.STRONG) is A.WORKED_EXAMPLE


def test_empty_cell_is_rejected():
    cells = copy.deepcopy(DEFAULT_CELLS)
    del cells["confused"]["partial"]
    with pytest.raises(DecisionTableError):
        DecisionController(cells)


def test_insufficient_column_must_be_state_uncertainty():
    cells = copy.deepcopy(DEFAULT_CELLS)
    cells["uncertain"]["insufficient"] = "simplify"
    with pytest.raises(DecisionTableError):
        DecisionController(cells)


def test_missing_file_uses_default_table(tmp_path):
    controller = DecisionController.from_file(tmp_path / "missing.json")
    assert controller.decide("understanding", E.STRONG) is A.ADVANCE


def test_table_version_is_read_from_file(tmp_path):
    path = tmp_path / "table.json"
    path.write_text(json.dumps({"version": 3, "cells": DEFAULT_CELLS}), encoding="utf-8")
    assert DecisionController.from_file(path).version == 3
