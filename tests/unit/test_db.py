"""Tests for the database layer."""

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
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [row[0] for row in await cursor.fetchall()]
    assert "sessions" in tables
    assert "events" in tables
    assert "transcripts" in tables


async def test_create_session():
    session = await db.create_session(
        "sess-1", project_path="/home/user/myproject", model="opus"
    )
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
