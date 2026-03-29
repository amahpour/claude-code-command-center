"""Tests for the REST API endpoints."""

import pytest
from httpx import AsyncClient, ASGITransport

import server.db as db
from server.routes.api import router
from fastapi import FastAPI

# Create a test app with just the API router
test_app = FastAPI()
test_app.include_router(router)


@pytest.fixture(autouse=True)
async def setup_db():
    await db.init_db(":memory:")
    yield
    await db.close_db()


@pytest.fixture
async def client():
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health(client: AsyncClient):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_list_sessions_empty(client: AsyncClient):
    resp = await client.get("/api/sessions")
    assert resp.status_code == 200
    assert resp.json()["sessions"] == []


async def test_list_sessions_with_data(client: AsyncClient):
    await db.create_session("s1", project_path="/tmp/proj1")
    await db.update_session("s1", status="working")
    await db.create_session("s2", project_path="/tmp/proj2")

    resp = await client.get("/api/sessions")
    assert resp.status_code == 200
    sessions = resp.json()["sessions"]
    assert len(sessions) == 2
    # working should come first
    assert sessions[0]["id"] == "s1"


async def test_get_session(client: AsyncClient):
    await db.create_session("s1", project_path="/tmp/proj")
    await db.add_event("s1", "SessionStart", payload={"test": True})

    resp = await client.get("/api/sessions/s1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["session"]["id"] == "s1"
    assert len(data["events"]) == 1


async def test_get_session_not_found(client: AsyncClient):
    resp = await client.get("/api/sessions/nonexistent")
    assert resp.status_code == 404


async def test_get_transcript(client: AsyncClient):
    await db.create_session("t1")
    await db.add_transcript("t1", "user", "Hello")
    await db.add_transcript("t1", "assistant", "Hi there!")

    resp = await client.get("/api/sessions/t1/transcript")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["transcripts"]) == 2


async def test_get_transcript_not_found(client: AsyncClient):
    resp = await client.get("/api/sessions/nonexistent/transcript")
    assert resp.status_code == 404


async def test_receive_hook(client: AsyncClient):
    resp = await client.post("/api/hooks", json={
        "event_type": "SessionStart",
        "session_id": "hook-1",
        "cwd": "/tmp/myproject",
        "model": "opus",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["session"]["project_name"] == "myproject"


async def test_receive_hook_no_session_id(client: AsyncClient):
    resp = await client.post("/api/hooks", json={
        "event_type": "SessionStart",
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


async def test_history(client: AsyncClient):
    for i in range(5):
        await db.create_session(f"h{i}")

    resp = await client.get("/api/history?limit=3&offset=0")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["sessions"]) == 3
    assert data["limit"] == 3


async def test_search(client: AsyncClient):
    await db.create_session("search-1")
    await db.add_transcript("search-1", "user", "Fix the authentication bug")
    await db.add_transcript("search-1", "assistant", "Looking at the database")

    resp = await client.get("/api/search?q=authentication")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert "authentication" in data["results"][0]["content"]


async def test_search_empty_query(client: AsyncClient):
    resp = await client.get("/api/search?q=")
    assert resp.status_code == 422  # Validation error


async def test_analytics_summary(client: AsyncClient):
    await db.create_session("a1")
    await db.update_session("a1", cost_usd=1.5, input_tokens=1000)

    resp = await client.get("/api/analytics/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_sessions"] == 1
    assert data["total_cost"] == 1.5


async def test_analytics_daily(client: AsyncClient):
    await db.create_session("d1")

    resp = await client.get("/api/analytics/daily?days=7")
    assert resp.status_code == 200
    data = resp.json()
    assert "days" in data
