"""
Tests for Evaluation Mode (SRS 3.2.9) and the evaluator routes.
"""

import asyncio
import time
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.models import Citation
from app.services.decision_controller import DecisionController
from app.services.evaluation import (
    EvaluationManager,
    Pipeline,
    QuestionFileError,
    RateLimitedLLM,
    classify_outcome,
    parse_questions,
    read_results,
    run,
    summarise,
)
from app.services.evidence_assessor import EvidenceAssessor
from app.services.prompt_builder import PromptBuilder

CSV = (
    "id,question,answerable,reference_page,learner_state\n"
    "Q01,What is a use case?,yes,3,confused\n"
    "Q02,What is the capital of France?,no,,understanding\n"
)


# ── Question file and refusal detection ──────────────────────────────

def test_question_file_is_read():
    questions = parse_questions(CSV)
    assert [q.id for q in questions] == ["Q01", "Q02"]
    assert questions[0].answerable and questions[0].reference_page == 3
    assert questions[1].learner_state == "understanding"


def test_question_file_with_missing_column_is_rejected():
    with pytest.raises(QuestionFileError, match="answerable"):
        parse_questions("id,question\nQ01,What?\n")


def test_unknown_learner_state_is_rejected():
    with pytest.raises(QuestionFileError, match="Line 2"):
        parse_questions("id,question,answerable,learner_state\nQ01,What?,yes,sleepy\n")


@pytest.mark.parametrize("answer", [
    "The provided passages do not contain information regarding the capital of France.",
    "The provided documents do not list the specific phases.",
    "The passages do not state who invented it.",
    "I could not find a reliable answer to this in your study material.",
])
def test_refusals_are_recognised(answer):
    assert classify_outcome("simplify", answer) == "refused"


def test_answer_and_actions_are_classified():
    assert classify_outcome("", "A use case describes how a user interacts with the system.") == "answered"
    assert classify_outcome("state_uncertainty", "anything") == "refused"
    assert classify_outcome("clarify", "Which part do you mean?") == "clarified"


# ── Rate limiter ─────────────────────────────────────────────────────

class EchoLLM:
    async def generate_stream(self, messages, context=""):
        yield "ok"


@pytest.mark.asyncio
async def test_rate_limiter_spaces_calls():
    llm = RateLimitedLLM(EchoLLM(), min_interval=0.2)
    started = time.monotonic()
    for _ in range(3):
        async for _chunk in llm.generate_stream([], context=""):
            pass
    assert time.monotonic() - started >= 0.4  # two gaps of 0.2 s


# ── A full run with a fake retriever and a fake AI service ───────────

class FakeRetriever:
    async def retrieve(self, query, top_k=5, document_ids=None):
        if "France" in query:
            return [Citation(document_id="d", document_name="se.pdf", page_number=9, text="unrelated", score=0.12)]
        return [
            Citation(document_id="d", document_name="se.pdf", page_number=3, text="use case text", score=0.82),
            Citation(document_id="d", document_name="se.pdf", page_number=4, text="more", score=0.61),
        ]


class FakeLLM:
    def __init__(self):
        self.calls = 0

    async def generate_stream(self, messages, context=""):
        self.calls += 1
        content = " ".join(m.content for m in messages)
        if "contradict" in content:
            yield "NO"
        elif "unrelated" in content:
            yield "The provided passages do not contain this information."
        else:
            yield "A use case describes an interaction."


def fake_pipeline(llm=None):
    llm = llm or FakeLLM()
    return Pipeline(
        retriever=FakeRetriever(),
        evidence=EvidenceAssessor(llm=llm),
        decision=DecisionController(),
        prompt_builder=PromptBuilder(),
        llm=llm,
        retry_waits=(),
    )


VERSIONS = ["full", "baseline_a", "baseline_b", "baseline_c"]


@pytest.mark.asyncio
async def test_run_compares_the_versions(tmp_path):
    questions = parse_questions(CSV)
    out = tmp_path / "results.csv"
    await run(fake_pipeline(), questions, VERSIONS, out)

    rows = read_results(out)
    assert len(rows) == 8
    summary = {s["version"]: s for s in summarise(rows, {"Q01", "Q02"}, VERSIONS)}
    assert summary["Full tutor"]["correct_refusals"] == 1
    assert summary["Full tutor"]["unsupported_answers"] == 0
    assert summary["Baseline A: normal RAG"]["correct_refusals"] == 1  # the LLM said "do not contain"
    full_q2 = next(r for r in rows if r["id"] == "Q02" and r["version"] == "full")
    assert full_q2["teaching_action"] == "state_uncertainty"  # refused by the evidence rule


@pytest.mark.asyncio
async def test_a_stopped_run_continues_where_it_stopped(tmp_path):
    questions = parse_questions(CSV)
    out = tmp_path / "results.csv"
    calls = {"n": 0}

    def stop_after_three():
        calls["n"] += 1
        return calls["n"] > 3

    await run(fake_pipeline(), questions, VERSIONS, out, should_stop=stop_after_three)
    assert len(read_results(out)) == 3

    llm = FakeLLM()
    await run(fake_pipeline(llm), questions, VERSIONS, out)
    assert len(read_results(out)) == 8  # only the five missing rows were added


# ── Evaluation manager (what the screen uses) ────────────────────────

class FakeMetadata:
    def __init__(self, docs):
        self.docs = docs

    async def list_documents(self, limit=50, offset=0):
        return self.docs, len(self.docs)


def make_manager(tmp_path, docs):
    return EvaluationManager(
        tmp_path / "evaluation",
        get_chat_service=lambda: None,
        get_metadata_store=lambda: FakeMetadata(docs),
        pipeline_factory=lambda chat, ids, delay, top_k=5: fake_pipeline(),
    )


READY_DOC = SimpleNamespace(id="d", original_name="se.pdf", status=SimpleNamespace(value="completed"))


@pytest.mark.asyncio
async def test_manager_runs_in_the_background_and_reports_progress(tmp_path):
    manager = make_manager(tmp_path, [READY_DOC])
    assert manager.save_questions(CSV) == {"questions": 2, "answerable": 1, "unanswerable": 1}

    status = await manager.start(VERSIONS, delay=0)
    assert status["status"] == "running" and status["total"] == 8
    await manager._task

    status = manager.status()
    assert status["status"] == "finished" and status["done"] == 8
    assert status["documents"] == ["se.pdf"]
    assert manager.summary_path.exists()
    assert len(manager.summary()) == 4


@pytest.mark.asyncio
async def test_manager_refuses_to_start_without_questions_or_documents(tmp_path):
    manager = make_manager(tmp_path, [])
    with pytest.raises(ValueError, match="question file"):
        await manager.start(VERSIONS, delay=0)
    manager.save_questions(CSV)
    with pytest.raises(ValueError, match="No completed document"):
        await manager.start(VERSIONS, delay=0)


@pytest.mark.asyncio
async def test_fresh_run_discards_earlier_results(tmp_path):
    manager = make_manager(tmp_path, [READY_DOC])
    manager.save_questions(CSV)
    await manager.start(["full"], delay=0)
    await manager._task
    await manager.start(["full"], delay=0, fresh=True)
    await manager._task
    assert len(read_results(manager.results_path)) == 2


# ── HTTP routes: evaluator only ──────────────────────────────────────

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


async def token_of(client, email, password):
    return (await client.post("/api/auth/login", json={"email": email, "password": password})).json()["token"]


@pytest.mark.asyncio
async def test_evaluation_routes_are_for_evaluators(client):
    admin = await token_of(client, "admin@ned.edu", "admin-pass-1")
    await client.post("/api/admin/evaluators", headers={"Authorization": f"Bearer {admin}"},
                      json={"full_name": "Eva", "email": "eva@ned.edu", "password": "password1"})
    await client.post("/api/auth/signup", json={"full_name": "Sara", "email": "s@ned.edu", "password": "password1"})
    evaluator = {"Authorization": f"Bearer {await token_of(client, 'eva@ned.edu', 'password1')}"}
    student = {"Authorization": f"Bearer {await token_of(client, 's@ned.edu', 'password1')}"}

    assert (await client.get("/api/evaluation/status")).status_code == 401
    assert (await client.get("/api/evaluation/status", headers=student)).status_code == 403

    bad = await client.post("/api/evaluation/questions", headers=evaluator,
                            files={"file": ("q.csv", b"id,question\nQ01,What?\n", "text/csv")})
    assert bad.status_code == 422

    ok = await client.post("/api/evaluation/questions", headers=evaluator,
                           files={"file": ("q.csv", CSV.encode(), "text/csv")})
    assert ok.status_code == 200 and ok.json()["questions"] == 2

    no_docs = await client.post("/api/evaluation/run", headers=evaluator, json={"delay": 0})
    assert no_docs.status_code == 400

    assert (await client.get("/api/evaluation/status", headers=evaluator)).json()["status"] == "idle"
    assert (await client.get("/api/evaluation/download/results", headers=evaluator)).status_code == 404
