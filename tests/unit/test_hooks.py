"""Tests for hook event processing."""

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest
import server.db as db
from server.hooks import process_hook_event


@pytest.fixture(autouse=True)
async def setup_db():
    await db.init_db(":memory:")
    yield
    await db.close_db()


async def test_session_start():
    result = await process_hook_event({
        "event_type": "SessionStart",
        "session_id": "sess-1",
        "cwd": "/home/user/myproject",
        "model": "opus",
    })
    assert result is not None
    assert result["id"] == "sess-1"
    assert result["project_name"] == "myproject"
    assert result["model"] == "opus"
    assert result["status"] == "idle"


async def test_pre_tool_use():
    await process_hook_event({
        "event_type": "SessionStart",
        "session_id": "sess-2",
        "cwd": "/tmp/test",
    })
    result = await process_hook_event({
        "event_type": "PreToolUse",
        "session_id": "sess-2",
        "tool_name": "Read",
    })
    assert result["status"] == "working"


async def test_post_tool_use():
    await process_hook_event({
        "event_type": "SessionStart",
        "session_id": "sess-3",
        "cwd": "/tmp/test",
    })
    await process_hook_event({
        "event_type": "PreToolUse",
        "session_id": "sess-3",
        "tool_name": "Write",
    })
    result = await process_hook_event({
        "event_type": "PostToolUse",
        "session_id": "sess-3",
        "tool_name": "Write",
    })
    # PostToolUse doesn't change status, just updates activity
    assert result is not None


async def test_stop_event():
    await process_hook_event({
        "event_type": "SessionStart",
        "session_id": "sess-4",
        "cwd": "/tmp/test",
    })
    await process_hook_event({
        "event_type": "PreToolUse",
        "session_id": "sess-4",
    })
    result = await process_hook_event({
        "event_type": "Stop",
        "session_id": "sess-4",
        "cost_usd": 0.05,
        "input_tokens": 1000,
        "output_tokens": 500,
    })
    assert result["status"] == "idle"
    assert result["cost_usd"] == 0.05
    assert result["input_tokens"] == 1000


async def test_session_end():
    await process_hook_event({
        "event_type": "SessionStart",
        "session_id": "sess-5",
        "cwd": "/tmp/test",
    })
    result = await process_hook_event({
        "event_type": "SessionEnd",
        "session_id": "sess-5",
    })
    assert result["status"] == "completed"
    assert result["ended_at"] is not None


async def test_notification_event():
    await process_hook_event({
        "event_type": "SessionStart",
        "session_id": "sess-6",
        "cwd": "/tmp/test",
    })
    result = await process_hook_event({
        "event_type": "Notification",
        "session_id": "sess-6",
        "message": "Waiting for permission to write file",
    })
    assert result["status"] == "waiting"
    assert result["task_description"] == "Waiting for permission to write file"


async def test_subagent_events():
    await process_hook_event({
        "event_type": "SessionStart",
        "session_id": "sess-7",
        "cwd": "/tmp/test",
    })
    result = await process_hook_event({
        "event_type": "SubagentStart",
        "session_id": "sess-7",
    })
    assert result is not None

    result = await process_hook_event({
        "event_type": "SubagentStop",
        "session_id": "sess-7",
    })
    assert result is not None


async def test_unknown_event_creates_session():
    result = await process_hook_event({
        "event_type": "SomeNewEvent",
        "session_id": "sess-8",
    })
    assert result is not None
    session = await db.get_session("sess-8")
    assert session is not None


async def test_missing_session_id():
    result = await process_hook_event({
        "event_type": "SessionStart",
    })
    assert result is None


async def test_events_recorded():
    await process_hook_event({
        "event_type": "SessionStart",
        "session_id": "sess-9",
        "cwd": "/tmp/test",
    })
    await process_hook_event({
        "event_type": "PreToolUse",
        "session_id": "sess-9",
        "tool_name": "Bash",
    })
    events = await db.get_session_events("sess-9")
    assert len(events) == 2
    types = [e["event_type"] for e in events]
    assert "SessionStart" in types
    assert "PreToolUse" in types


async def test_stale_session_detection():
    """Test that sessions with old activity are marked stale."""
    await db.create_session("stale-1")
    old_time = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    await db.update_session("stale-1", status="idle", last_activity_at=old_time)

    # Manually run the check logic
    sessions = await db.get_all_active_sessions()
    now = datetime.now(timezone.utc)
    for session in sessions:
        if session["status"] in ("completed", "stale"):
            continue
        last = session.get("last_activity_at")
        if last:
            last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            diff = (now - last_dt).total_seconds()
            if diff > 300:
                await db.update_session(session["id"], status="stale")

    session = await db.get_session("stale-1")
    assert session["status"] == "stale"


async def test_stop_with_context_tracking():
    await process_hook_event({
        "event_type": "SessionStart",
        "session_id": "ctx-1",
        "cwd": "/tmp/test",
    })
    result = await process_hook_event({
        "event_type": "Stop",
        "session_id": "ctx-1",
        "context_tokens": 50000,
        "context_max": 200000,
    })
    assert result["context_tokens"] == 50000
    assert result["context_usage_percent"] == 25.0


async def test_git_branch_extraction():
    """Test that git branch is extracted on SessionStart."""
    with patch("server.hooks._extract_git_branch", return_value="feature/test"):
        result = await process_hook_event({
            "event_type": "SessionStart",
            "session_id": "git-1",
            "cwd": "/tmp/test",
        })
        assert result["git_branch"] == "feature/test"
