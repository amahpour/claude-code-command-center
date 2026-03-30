"""Tests for the database layer."""

from unittest.mock import patch

import pytest

import server.db as db


@pytest.fixture(autouse=True)
async def setup_db():
    """Initialize an in-memory database for each test."""
    await db.init_db(":memory:")
    yield
    await db.close_db()


async def test_tables_created():
    """Tables should exist after init."""
    conn = await db.get_db()
    cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [row[0] for row in await cursor.fetchall()]
    assert "sessions" in tables
    assert "events" in tables
    assert "transcripts" in tables


async def test_create_session():
    session = await db.create_session("sess-1", project_path="/home/user/myproject", model="opus")
    assert session["id"] == "sess-1"
    assert session["project_name"] == "myproject"
    assert session["model"] == "opus"
    assert session["status"] == "idle"


async def test_get_session():
    await db.create_session("sess-2", project_path="/tmp/foo")
    session = await db.get_session("sess-2")
    assert session is not None
    assert session["project_name"] == "foo"


async def test_get_session_not_found():
    session = await db.get_session("nonexistent")
    assert session is None


async def test_update_session():
    await db.create_session("sess-3")
    updated = await db.update_session("sess-3", status="working", cost_usd=1.5)
    assert updated["status"] == "working"
    assert updated["cost_usd"] == 1.5


async def test_get_all_active_sessions():
    await db.create_session("s1")
    await db.update_session("s1", status="working")
    await db.create_session("s2")
    await db.update_session("s2", status="idle")
    await db.create_session("s3")
    await db.update_session("s3", status="completed")

    active = await db.get_all_active_sessions()
    ids = [s["id"] for s in active]
    assert "s1" in ids
    assert "s2" in ids
    assert "s3" not in ids  # completed sessions excluded
    # working should come before idle
    assert ids.index("s1") < ids.index("s2")


async def test_get_all_sessions_pagination():
    for i in range(10):
        await db.create_session(f"p{i}")
    all_sessions = await db.get_all_sessions(limit=5, offset=0)
    assert len(all_sessions) == 5
    page2 = await db.get_all_sessions(limit=5, offset=5)
    assert len(page2) == 5


async def test_add_event():
    await db.create_session("sess-ev")
    eid = await db.add_event("sess-ev", "PreToolUse", tool_name="Read", payload={"file": "test.py"})
    assert eid is not None
    events = await db.get_session_events("sess-ev")
    assert len(events) == 1
    assert events[0]["event_type"] == "PreToolUse"
    assert events[0]["tool_name"] == "Read"


async def test_add_transcript():
    await db.create_session("sess-tr")
    tid = await db.add_transcript("sess-tr", "user", "Fix the auth bug")
    assert tid is not None
    transcripts = await db.get_session_transcripts("sess-tr")
    assert len(transcripts) == 1
    assert transcripts[0]["role"] == "user"
    assert transcripts[0]["content"] == "Fix the auth bug"


async def test_fts_search():
    await db.create_session("sess-fts")
    await db.add_transcript("sess-fts", "user", "Fix the authentication bug in the login module")
    await db.add_transcript("sess-fts", "assistant", "I will look at the database schema")

    results = await db.search_transcripts("authentication")
    assert len(results) == 1
    assert "authentication" in results[0]["content"]

    results2 = await db.search_transcripts("database")
    assert len(results2) == 1


async def test_analytics_summary():
    await db.create_session("a1")
    await db.update_session("a1", cost_usd=2.5, input_tokens=1000, output_tokens=500)
    await db.create_session("a2")
    await db.update_session("a2", cost_usd=1.0, input_tokens=800, output_tokens=300)

    summary = await db.get_analytics_summary()
    assert summary["total_sessions"] == 2
    assert summary["total_cost"] == 3.5
    assert summary["total_input_tokens"] == 1800


async def test_analytics_daily():
    await db.create_session("d1")
    await db.update_session("d1", cost_usd=1.0)

    daily = await db.get_analytics_daily(days=7)
    assert len(daily) >= 1
    assert daily[0]["session_count"] >= 1


async def test_completed_sessions():
    await db.create_session("c1")
    await db.update_session("c1", status="completed", ended_at="2026-01-01T00:00:00Z")
    await db.create_session("c2")
    await db.update_session("c2", status="idle")

    completed = await db.get_completed_sessions()
    assert len(completed) == 1
    assert completed[0]["id"] == "c1"


async def test_update_session_no_kwargs():
    """update_session with no kwargs returns session unchanged."""
    await db.create_session("noop-1")
    result = await db.update_session("noop-1")
    assert result is not None
    assert result["id"] == "noop-1"


async def test_get_setting_and_set_setting():
    """Test setting CRUD operations."""
    val = await db.get_setting("test_key")
    assert val is None

    await db.set_setting("test_key", "test_value")
    val = await db.get_setting("test_key")
    assert val == "test_value"

    await db.set_setting("test_key", "new_value")
    val = await db.get_setting("test_key")
    assert val == "new_value"


async def test_get_all_settings():
    """Test getting all settings."""
    await db.set_setting("k1", "v1")
    await db.set_setting("k2", "v2")
    settings = await db.get_all_settings()
    assert settings["k1"] == "v1"
    assert settings["k2"] == "v2"


async def test_get_db_without_init():
    """get_db raises RuntimeError when not initialized."""
    await db.close_db()
    with pytest.raises(RuntimeError, match="Database not initialized"):
        await db.get_db()
    # Re-init for cleanup
    await db.init_db(":memory:")


def test_get_db_path_default():
    """Test _get_db_path returns default path."""
    import os

    with patch.dict(os.environ, {}, clear=True):
        path = db._get_db_path()
        assert "data.db" in path


def test_get_db_path_from_env():
    """Test _get_db_path respects env override."""
    import os

    with patch.dict(os.environ, {"CCCC_DB_PATH": "/tmp/test.db"}):
        assert db._get_db_path() == "/tmp/test.db"


async def test_init_db_with_file_path():
    """Test init_db creates directory for file-based DB."""
    import os
    import tempfile

    await db.close_db()
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "subdir", "test.db")
        await db.init_db(path)
        assert os.path.isfile(path)
        await db.close_db()
    # Re-init for cleanup
    await db.init_db(":memory:")


async def test_display_name_locked_column():
    """display_name_locked column should exist and default to 0."""
    session = await db.create_session("lock-col-1")
    assert session["display_name_locked"] == 0
    updated = await db.update_session("lock-col-1", display_name_locked=1)
    assert updated["display_name_locked"] == 1


async def test_last_activity_preview_column():
    """last_activity_preview column should exist and default to None."""
    session = await db.create_session("preview-col-1")
    assert session.get("last_activity_preview") is None
    updated = await db.update_session("preview-col-1", last_activity_preview="Editing file.py")
    assert updated["last_activity_preview"] == "Editing file.py"


async def test_subagent_columns_exist():
    """parent_session_id and agent_type columns should be stored correctly."""
    session = await db.create_session("sub-col-1")
    assert session.get("parent_session_id") is None
    assert session.get("agent_type") is None

    updated = await db.update_session("sub-col-1", parent_session_id="parent-1", agent_type="codegen")
    assert updated["parent_session_id"] == "parent-1"
    assert updated["agent_type"] == "codegen"


async def test_get_subagents_for_session():
    """get_subagents_for_session returns subagents for a given parent."""
    await db.create_session("parent-sub-1")
    await db.create_session("child-1")
    await db.update_session("child-1", parent_session_id="parent-sub-1", agent_type="codegen")
    await db.create_session("child-2")
    await db.update_session("child-2", parent_session_id="parent-sub-1", agent_type="research")

    subagents = await db.get_subagents_for_session("parent-sub-1")
    assert len(subagents) == 2
    ids = [s["id"] for s in subagents]
    assert "child-1" in ids
    assert "child-2" in ids


async def test_get_subagents_by_parent():
    """get_subagents_by_parent groups subagents by their parent session ID."""
    await db.create_session("p1")
    await db.create_session("p2")
    await db.create_session("p1-child-a")
    await db.update_session("p1-child-a", parent_session_id="p1", agent_type="codegen")
    await db.create_session("p1-child-b")
    await db.update_session("p1-child-b", parent_session_id="p1", agent_type="research")
    await db.create_session("p2-child-a")
    await db.update_session("p2-child-a", parent_session_id="p2", agent_type="codegen")

    result = await db.get_subagents_by_parent(["p1", "p2"])
    assert len(result["p1"]) == 2
    assert len(result["p2"]) == 1
    p1_ids = [s["id"] for s in result["p1"]]
    assert "p1-child-a" in p1_ids
    assert "p1-child-b" in p1_ids
    assert result["p2"][0]["id"] == "p2-child-a"


async def test_active_sessions_exclude_subagents():
    """get_all_active_sessions should not return sessions with a parent_session_id."""
    await db.create_session("active-parent")
    await db.update_session("active-parent", status="working")
    await db.create_session("active-child")
    await db.update_session("active-child", status="working", parent_session_id="active-parent")

    active = await db.get_all_active_sessions()
    ids = [s["id"] for s in active]
    assert "active-parent" in ids
    assert "active-child" not in ids
