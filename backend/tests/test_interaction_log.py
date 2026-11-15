"""
Tests for the interaction log (SRS LOG module) and the administrator's log routes.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock

from app.core.models import ChatRequest, Citation, Conversation
from app.services.chat_service import STATE_UNCERTAINTY_MESSAGE, ChatService
from app.services.decision_controller import DecisionController
from app.services.evidence_assessor import EvidenceAssessor
from app.services.learner_state_stub import FixedLearnerStateEstimator
from app.services.prompt_builder import PromptBuilder
from app.storage.interaction_log_store import SQLiteInteractionLogStore
from app.storage.user_store import SQLiteUserStore


def cite(score, page=1):
    return Citation(document_id="doc1", document_name="notes.pdf", page_number=page, text="passage", score=score)


class AnsweringLLM:
    async def generate_stream(self, messages, context):
        yield "NO" if "contradict" in messages[0].content else "Here is the answer."


class BrokenLog:
    async def add(self, **kwargs):
        raise RuntimeError("disk full")


@pytest_asyncio.fixture
async def log(tmp_path):
    db = str(tmp_path / "metadata.db")
    await SQLiteUserStore(db).initialize()  # the log joins the users table for the email
    store = SQLiteInteractionLogStore(db)
    await store.initialize()
    return store


def make_service(citations, interaction_log):
    store = AsyncMock()
    store.create_conversation.return_value = Conversation(id="conv1", title="New Conv")
    store.get_conversation.return_value = None
    retriever = AsyncMock()
    retriever.retrieve.return_value = citations
    llm = AnsweringLLM()
    return ChatService(
        retriever=retriever,
        prompt_builder=PromptBuilder(),
        llm=llm,
        conversation_store=store,
        evidence_assessor=EvidenceAssessor(llm=llm),
        learner_estimator=FixedLearnerStateEstimator(),
        decision_controller=DecisionController(),
        interaction_log=interaction_log,
    )


async def ask(service, message, user_id=7):
    return "".join([c async for c, _, _ in service.chat_stream(ChatRequest(message=message), user_id=user_id)])


# ── Logging from the chat pipeline ───────────────────────────────────

@pytest.mark.asyncio
async def test_answered_question_is_logged_with_its_tutor_state(log):
    await ask(make_service([cite(0.9), cite(0.8, page=2)], log), "What is a use case?")

    items, total = await log.list_entries()
    assert total == 1
    row = items[0]
    assert row["user_id"] == 7 and row["conversation_id"] == "conv1"
    assert row["question"] == "What is a use case?"
    assert row["evidence_state"] == "strong" and row["evidence_score"] == 0.9
    assert row["learner_state"] and row["teaching_action"] and row["table_version"]
    assert row["topic"] == "doc1:0"
    assert row["answered"] is True and row["answer_chars"] > 0


@pytest.mark.asyncio
async def test_question_without_evidence_is_logged_as_unanswered(log):
    text = await ask(make_service([], log), "Who won the cricket match?")
    assert text == STATE_UNCERTAINTY_MESSAGE

    items, total = await log.list_entries(unanswered_only=True)
    assert total == 1
    assert items[0]["teaching_action"] == "state_uncertainty"
    assert items[0]["evidence_state"] == "insufficient"
    assert items[0]["answered"] is False


@pytest.mark.asyncio
async def test_a_broken_log_does_not_break_the_answer():
    text = await ask(make_service([cite(0.9), cite(0.8)], BrokenLog()), "What is a use case?")
    assert text == "Here is the answer."


@pytest.mark.asyncio
async def test_no_log_configured_still_answers():
    text = await ask(make_service([cite(0.9), cite(0.8)], None), "What is a use case?")
    assert text == "Here is the answer."


# ── Reading with filters ─────────────────────────────────────────────

async def add_row(log, user_id, answered, question="q"):
    await log.add(
        user_id=user_id, conversation_id="c", question=question, topic=None,
        learner_state="uncertain", evidence_state="partial", evidence_score=0.6,
        teaching_action="clarify", table_version="1", answered=answered, answer_chars=10,
    )


@pytest.mark.asyncio
async def test_filters_by_user_unanswered_and_date(log):
    await add_row(log, 1, True, "first")
    await add_row(log, 2, False, "second")
    await add_row(log, 1, False, "third")

    items, total = await log.list_entries()
    assert total == 3 and [i["question"] for i in items] == ["third", "second", "first"]  # newest first

    _, total = await log.list_entries(user_id=1)
    assert total == 2
    items, total = await log.list_entries(user_id=1, unanswered_only=True)
    assert total == 1 and items[0]["question"] == "third"

    _, total = await log.list_entries(date_from="2000-01-01", date_to="2999-12-31")
    assert total == 3
    _, total = await log.list_entries(date_to="2000-01-01")
    assert total == 0

    items, total = await log.list_entries(limit=1, offset=1)
    assert total == 3 and len(items) == 1 and items[0]["question"] == "second"


# ── Administrator routes ─────────────────────────────────────────────

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


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


async def login(client, email, password):
    return (await client.post("/api/auth/login", json={"email": email, "password": password})).json()["token"]


@pytest.mark.asyncio
async def test_log_routes_are_for_the_administrator_only(client):
    from app.dependencies import get_interaction_log

    await client.post("/api/auth/signup", json={"full_name": "Sara", "email": "s@ned.edu", "password": "password1"})
    student = await login(client, "s@ned.edu", "password1")
    admin = await login(client, "admin@ned.edu", "admin-pass-1")
    me = (await client.get("/api/auth/me", headers=bearer(student))).json()

    log = get_interaction_log()
    await add_row(log, me["id"], True, "answered one")
    await add_row(log, me["id"], False, "unanswered one")

    for path in ("/api/admin/logs", "/api/admin/unanswered"):
        assert (await client.get(path)).status_code == 401
        assert (await client.get(path, headers=bearer(student))).status_code == 403

    r = await client.get("/api/admin/logs", headers=bearer(admin))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert body["items"][0]["user_email"] == "s@ned.edu"

    r = await client.get("/api/admin/unanswered", headers=bearer(admin))
    assert [i["question"] for i in r.json()["items"]] == ["unanswered one"]

    r = await client.get("/api/admin/logs", headers=bearer(admin), params={"date_from": "15-11-2026"})
    assert r.status_code == 422
