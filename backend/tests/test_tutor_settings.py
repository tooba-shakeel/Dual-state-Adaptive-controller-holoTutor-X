"""
Tests for Tutor Settings: the administrator changes the evidence limits and the
decision table while the app runs; every change is a new table version.
"""

import copy
import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.models import Citation
from app.core.tutor_models import EvidenceState
from app.services.decision_controller import DEFAULT_CELLS, DecisionController
from app.services.evidence_assessor import EvidenceAssessor
from app.services.tutor_settings import TutorSettingsError, TutorSettingsService


def cite(score):
    return Citation(document_id="d", document_name="n.pdf", page_number=1, text="t", score=score)


def make(tmp_path):
    assessor = EvidenceAssessor(llm=None)
    controller = DecisionController()
    service = TutorSettingsService(
        tmp_path / "tutor_settings.json", assessor, controller,
        default_limits=(0.50, 0.75), default_cells=DEFAULT_CELLS,
    )
    return service, assessor, controller


def changed_cells():
    cells = copy.deepcopy(DEFAULT_CELLS)
    cells["understanding"]["partial"] = "advance"
    return cells


@pytest.mark.asyncio
async def test_new_limits_change_how_evidence_is_rated(tmp_path):
    service, assessor, _ = make(tmp_path)
    assert (await assessor.assess("q", [cite(0.55)]))[0] is EvidenceState.PARTIAL

    service.update(0.60, 0.80, DEFAULT_CELLS, "admin@ned.edu")
    assert (await assessor.assess("q", [cite(0.55)]))[0] is EvidenceState.INSUFFICIENT


def test_new_table_is_used_and_gets_the_next_version(tmp_path):
    service, _, controller = make(tmp_path)
    assert controller.decide("understanding", EvidenceState.PARTIAL).value == "clarify"

    result = service.update(0.50, 0.75, changed_cells(), "admin@ned.edu")
    assert result["version"] == 2 and result["updated_by"] == "admin@ned.edu"
    assert controller.version == 2
    assert controller.decide("understanding", EvidenceState.PARTIAL).value == "advance"
    assert result["history"][0]["version"] == 1


def test_saving_the_same_settings_keeps_the_version(tmp_path):
    service, _, controller = make(tmp_path)
    service.update(0.50, 0.75, DEFAULT_CELLS, "admin@ned.edu")
    assert controller.version == 1


@pytest.mark.parametrize("partial, strong, cells", [
    (0.80, 0.50, DEFAULT_CELLS),                                    # partial above strong
    (0.50, 0.75, {**DEFAULT_CELLS, "confused": {**DEFAULT_CELLS["confused"], "insufficient": "simplify"}}),
    (0.50, 0.75, {**DEFAULT_CELLS, "confused": {**DEFAULT_CELLS["confused"], "strong": "dance"}}),
    (0.50, 0.75, {k: v for k, v in DEFAULT_CELLS.items() if k != "confused"}),  # missing row
])
def test_bad_settings_are_rejected_and_nothing_changes(tmp_path, partial, strong, cells):
    service, assessor, controller = make(tmp_path)
    with pytest.raises(TutorSettingsError):
        service.update(partial, strong, cells, "admin@ned.edu")
    assert (assessor.partial_limit, assessor.strong_limit) == (0.50, 0.75)
    assert controller.version == 1 and controller.cells == DEFAULT_CELLS
    assert not (tmp_path / "tutor_settings.json").exists()


def test_saved_settings_come_back_after_a_restart(tmp_path):
    service, _, _ = make(tmp_path)
    service.update(0.55, 0.80, changed_cells(), "admin@ned.edu")

    again, assessor, controller = make(tmp_path)
    again.load()
    assert (assessor.partial_limit, assessor.strong_limit) == (0.55, 0.80)
    assert controller.version == 2
    assert controller.cells["understanding"]["partial"] == "advance"


def test_a_broken_settings_file_keeps_the_defaults(tmp_path):
    (tmp_path / "tutor_settings.json").write_text(json.dumps({"current": {"version": 9}}), encoding="utf-8")
    service, assessor, controller = make(tmp_path)
    service.load()
    assert controller.version == 1 and assessor.partial_limit == 0.50


def test_reset_goes_back_to_the_defaults_as_a_new_version(tmp_path):
    service, assessor, controller = make(tmp_path)
    service.update(0.60, 0.90, changed_cells(), "admin@ned.edu")
    result = service.reset("admin@ned.edu")
    assert result["version"] == 3
    assert (assessor.partial_limit, assessor.strong_limit) == (0.50, 0.75)
    assert controller.cells == DEFAULT_CELLS


# ── HTTP routes ──────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    from app.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "ADMIN_EMAIL", "admin@ned.edu")
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "admin-pass-1")
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac


async def token(client, email, password):
    return (await client.post("/api/auth/login", json={"email": email, "password": password})).json()["token"]


@pytest.mark.asyncio
async def test_routes_are_for_the_administrator_only(client):
    await client.post("/api/auth/signup", json={"full_name": "S", "email": "s@ned.edu", "password": "password1"})
    student = {"Authorization": f"Bearer {await token(client, 's@ned.edu', 'password1')}"}
    admin = {"Authorization": f"Bearer {await token(client, 'admin@ned.edu', 'admin-pass-1')}"}
    path = "/api/admin/tutor-settings"

    assert (await client.get(path)).status_code == 401
    assert (await client.get(path, headers=student)).status_code == 403

    current = (await client.get(path, headers=admin)).json()
    assert current["partial_limit"] == 0.50 and current["strong_limit"] == 0.75
    assert len(current["cells"]) == 4 and "state_uncertainty" in current["actions"]
    start = current["version"]

    body = {"partial_limit": 0.55, "strong_limit": 0.80, "cells": current["cells"]}
    assert (await client.put(path, headers=student, json=body)).status_code == 403
    r = await client.put(path, headers=admin, json=body)
    assert r.status_code == 200 and r.json()["version"] == start + 1

    bad = {**body, "partial_limit": 0.9}
    r = await client.put(path, headers=admin, json=bad)
    assert r.status_code == 422 and "partial_limit" in r.json()["detail"]

    r = await client.post(f"{path}/reset", headers=admin)
    assert r.json()["partial_limit"] == 0.50 and r.json()["version"] == start + 2
