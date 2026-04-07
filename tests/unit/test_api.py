"""Tests for the REST API endpoints."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import server.db as db
from server.routes.api import router

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


async def test_get_transcript_after_id(client: AsyncClient):
    await db.create_session("t-after")
    id1 = await db.add_transcript("t-after", "user", "First")
    await db.add_transcript("t-after", "assistant", "Second")
    id3 = await db.add_transcript("t-after", "user", "Third")

    # Fetch only entries after id1
    resp = await client.get(f"/api/sessions/t-after/transcript?after_id={id1}")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["transcripts"]) == 2
    assert data["transcripts"][0]["content"] == "Second"
    assert data["transcripts"][1]["content"] == "Third"

    # Fetch after the last entry — should return nothing
    resp = await client.get(f"/api/sessions/t-after/transcript?after_id={id3}")
    assert resp.status_code == 200
    assert len(resp.json()["transcripts"]) == 0


async def test_get_transcript_not_found(client: AsyncClient):
    resp = await client.get("/api/sessions/nonexistent/transcript")
    assert resp.status_code == 404


async def test_receive_hook(client: AsyncClient):
    resp = await client.post(
        "/api/hooks",
        json={
            "event_type": "SessionStart",
            "session_id": "hook-1",
            "cwd": "/tmp/myproject",
            "model": "opus",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["session"]["project_name"] == "myproject"


async def test_receive_hook_no_session_id(client: AsyncClient):
    resp = await client.post(
        "/api/hooks",
        json={
            "event_type": "SessionStart",
        },
    )
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


async def test_get_settings_empty(client: AsyncClient):
    resp = await client.get("/api/settings")
    assert resp.status_code == 200
    assert resp.json()["settings"] == {}


async def test_update_settings(client: AsyncClient):
    resp = await client.put(
        "/api/settings",
        json={
            "jira_project_keys": ["PROJ", "DEV"],
            "jira_server_url": "https://jira.example.com",
        },
    )
    assert resp.status_code == 200
    settings = resp.json()["settings"]
    assert settings["jira_project_keys"] == ["PROJ", "DEV"]
    assert settings["jira_server_url"] == "https://jira.example.com"


async def test_update_settings_summary_interval(client: AsyncClient):
    resp = await client.put(
        "/api/settings",
        json={"summary_interval": 10},
    )
    assert resp.status_code == 200
    settings = resp.json()["settings"]
    assert settings["summary_interval"] == 10


async def test_update_settings_summary_interval_invalid(client: AsyncClient):
    resp = await client.put(
        "/api/settings",
        json={"summary_interval": 0},
    )
    assert resp.status_code == 400


async def test_update_settings_summary_interval_negative(client: AsyncClient):
    resp = await client.put(
        "/api/settings",
        json={"summary_interval": -5},
    )
    assert resp.status_code == 400


async def test_update_settings_expanded_tile_items(client: AsyncClient):
    resp = await client.put(
        "/api/settings",
        json={"expanded_tile_items": 15},
    )
    assert resp.status_code == 200
    settings = resp.json()["settings"]
    assert settings["expanded_tile_items"] == 15


async def test_update_settings_expanded_tile_items_invalid(client: AsyncClient):
    resp = await client.put(
        "/api/settings",
        json={"expanded_tile_items": 0},
    )
    assert resp.status_code == 400


async def test_update_settings_expanded_tile_items_negative(client: AsyncClient):
    resp = await client.put(
        "/api/settings",
        json={"expanded_tile_items": -1},
    )
    assert resp.status_code == 400


async def test_get_settings_with_values(client: AsyncClient):
    await db.set_setting("jira_project_keys", '["PROJ"]')
    await db.set_setting("plain_key", "not-json")
    resp = await client.get("/api/settings")
    assert resp.status_code == 200
    settings = resp.json()["settings"]
    assert settings["jira_project_keys"] == ["PROJ"]
    assert settings["plain_key"] == "not-json"


async def test_patch_session_ticket_id(client: AsyncClient):
    await db.create_session("patch-1", project_path="/tmp/proj")
    resp = await client.patch("/api/sessions/patch-1", json={"ticket_id": "PROJ-123"})
    assert resp.status_code == 200
    assert resp.json()["session"]["ticket_id"] == "PROJ-123"


async def test_patch_session_display_name(client: AsyncClient):
    await db.create_session("patch-2", project_path="/tmp/proj")
    resp = await client.patch("/api/sessions/patch-2", json={"display_name": "My Session"})
    assert resp.status_code == 200
    assert resp.json()["session"]["display_name"] == "My Session"


async def test_patch_session_not_found(client: AsyncClient):
    resp = await client.patch("/api/sessions/nonexistent", json={"ticket_id": "X-1"})
    assert resp.status_code == 404


async def test_patch_session_no_updates(client: AsyncClient):
    await db.create_session("patch-3")
    resp = await client.patch("/api/sessions/patch-3", json={})
    assert resp.status_code == 200
    assert resp.json()["session"]["id"] == "patch-3"


async def test_browse_directory(client: AsyncClient):
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "subdir"))
        with open(os.path.join(d, "file.txt"), "w") as f:
            f.write("test")

        resp = await client.get(f"/api/browse?path={d}")
        assert resp.status_code == 200
        entries = resp.json()["entries"]
        names = [e["name"] for e in entries]
        assert "subdir" in names
        # Regular files should not appear (only dirs)
        assert "file.txt" not in names


async def test_browse_hidden_files_excluded(client: AsyncClient):
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, ".hidden"))
        os.makedirs(os.path.join(d, "visible"))

        resp = await client.get(f"/api/browse?path={d}")
        names = [e["name"] for e in resp.json()["entries"]]
        assert ".hidden" not in names
        assert "visible" in names


async def test_browse_invalid_path(client: AsyncClient):
    resp = await client.get("/api/browse?path=/nonexistent/path/xyz")
    assert resp.status_code == 400


async def test_browse_permission_denied(client: AsyncClient):
    from unittest.mock import patch

    with patch("os.listdir", side_effect=PermissionError), patch("os.path.isdir", return_value=True):
        resp = await client.get("/api/browse?path=/root")
        assert resp.status_code == 403


async def test_new_session_disabled(client: AsyncClient):
    resp = await client.post(
        "/api/sessions/new",
        json={
            "project_dir": "/tmp/proj",
            "prompt": "hello",
        },
    )
    assert resp.status_code == 501


async def test_patch_session_lock(client: AsyncClient):
    await db.create_session("lock-1", project_path="/tmp/proj")
    resp = await client.patch("/api/sessions/lock-1", json={"display_name_locked": True})
    assert resp.status_code == 200
    assert resp.json()["session"]["display_name_locked"] == 1


async def test_patch_session_unlock(client: AsyncClient):
    await db.create_session("lock-2", project_path="/tmp/proj")
    await db.update_session("lock-2", display_name_locked=1)
    resp = await client.patch("/api/sessions/lock-2", json={"display_name_locked": False})
    assert resp.status_code == 200
    assert resp.json()["session"]["display_name_locked"] == 0


async def test_list_sessions_includes_subagents(client: AsyncClient):
    """GET /api/sessions includes subagents nested inside their parent."""
    from server.hooks import process_hook_event

    await process_hook_event(
        {
            "event_type": "SessionStart",
            "session_id": "api-parent-1",
            "cwd": "/tmp/proj",
        }
    )
    await process_hook_event(
        {
            "event_type": "SubagentStart",
            "session_id": "api-parent-1",
            "agent_id": "api-sub-1",
            "agent_type": "codegen",
        }
    )

    resp = await client.get("/api/sessions")
    assert resp.status_code == 200
    sessions = resp.json()["sessions"]
    # The subagent should NOT appear as a top-level session
    top_ids = [s["id"] for s in sessions]
    assert "api-parent-1" in top_ids
    assert "api-sub-1" not in top_ids
    # The parent should have a subagents array
    parent = next(s for s in sessions if s["id"] == "api-parent-1")
    assert len(parent["subagents"]) == 1
    assert parent["subagents"][0]["id"] == "api-sub-1"


async def test_get_session_includes_subagents(client: AsyncClient):
    """GET /api/sessions/{id} includes subagents array."""
    from server.hooks import process_hook_event

    await process_hook_event(
        {
            "event_type": "SessionStart",
            "session_id": "api-detail-1",
            "cwd": "/tmp/proj",
        }
    )
    await process_hook_event(
        {
            "event_type": "SubagentStart",
            "session_id": "api-detail-1",
            "agent_id": "api-detail-sub-1",
            "agent_type": "research",
        }
    )

    resp = await client.get("/api/sessions/api-detail-1")
    assert resp.status_code == 200
    data = resp.json()
    session = data["session"]
    assert "subagents" in session
    assert len(session["subagents"]) == 1
    assert session["subagents"][0]["id"] == "api-detail-sub-1"
    assert session["subagents"][0]["agent_type"] == "research"
