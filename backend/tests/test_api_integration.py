"""
Integration tests for API endpoints.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.config import settings
from app.main import app


async def student_token(ac, email):
    await ac.post("/api/auth/signup", json={"full_name": "Student", "email": email, "password": "password1"})
    r = await ac.post("/api/auth/login", json={"email": email, "password": "password1"})
    return r.json()["token"]


@pytest_asyncio.fixture
async def anon(tmp_path, monkeypatch):
    """Async test client using httpx, with the app started on a temporary data folder (not logged in)."""
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    # ASGITransport does not run the lifespan, so start the services ourselves.
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


@pytest_asyncio.fixture
async def client(anon):
    """The same client, logged in as a student (documents and chat need a login)."""
    token = await student_token(anon, "student@ned.edu")
    anon.headers["Authorization"] = f"Bearer {token}"
    yield anon


@pytest.mark.asyncio
async def test_health_endpoint(client):
    """Health endpoint should return 200 with status."""
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "version" in data
    assert "components" in data


@pytest.mark.asyncio
async def test_list_documents_empty(client):
    """Document list should return empty list initially."""
    response = await client.get("/api/documents")
    assert response.status_code == 200
    data = response.json()
    assert "documents" in data
    assert isinstance(data["documents"], list)


@pytest.mark.asyncio
async def test_upload_validation_rejects_non_pdf(client):
    """Upload should reject non-PDF files."""
    response = await client.post(
        "/api/documents/upload",
        files={"files": ("test.txt", b"not a pdf", "text/plain")}
    )
    # Should return validation error (422)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_nonexistent_document(client):
    """Getting a non-existent document should return 404."""
    response = await client.get("/api/documents/nonexistent123")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_nonexistent_document(client):
    """Deleting a non-existent document should return 404."""
    response = await client.delete("/api/documents/nonexistent123")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_search_endpoint(client):
    """Search endpoint should return results array."""
    response = await client.get("/api/search", params={"query": "test"})
    assert response.status_code == 200
    data = response.json()
    assert "results" in data


# ── Each student sees only their own data (SRS 3.2.2, 3.2.8) ─────────

@pytest.mark.asyncio
async def test_documents_and_conversations_need_a_login(anon):
    assert (await anon.get("/api/documents")).status_code == 401
    assert (await anon.get("/api/conversations")).status_code == 401
    assert (await anon.post("/api/chat", json={"message": "hi"})).status_code == 401


@pytest.mark.asyncio
async def test_students_do_not_see_each_others_conversations(anon):
    a = {"Authorization": f"Bearer {await student_token(anon, 'a@ned.edu')}"}
    b = {"Authorization": f"Bearer {await student_token(anon, 'b@ned.edu')}"}

    r = await anon.post("/api/conversations", headers=a)
    assert r.status_code in (200, 201)
    conv_id = r.json()["id"]

    a_list = (await anon.get("/api/conversations", headers=a)).json()
    b_list = (await anon.get("/api/conversations", headers=b)).json()
    ids = lambda body: [c["id"] for c in (body["conversations"] if isinstance(body, dict) else body)]
    assert conv_id in ids(a_list)
    assert conv_id not in ids(b_list)

    assert (await anon.get(f"/api/conversations/{conv_id}", headers=a)).status_code == 200
    assert (await anon.get(f"/api/conversations/{conv_id}", headers=b)).status_code == 404
    r = await anon.post("/api/chat", headers=b, json={"message": "hi", "conversation_id": conv_id})
    assert r.status_code == 404
