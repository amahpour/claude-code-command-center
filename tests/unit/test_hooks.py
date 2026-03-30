"""Tests for hook event processing."""

import asyncio
import subprocess
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import server.db as db
from server.hooks import process_hook_event


@pytest.fixture(autouse=True)
async def setup_db():
    await db.init_db(":memory:")
    yield
    await db.close_db()


async def test_session_start():
    result = await process_hook_event(
        {
            "event_type": "SessionStart",
            "session_id": "sess-1",
            "cwd": "/home/user/myproject",
            "model": "opus",
        }
    )
    assert result is not None
    assert result["id"] == "sess-1"
    assert result["project_name"] == "myproject"
    assert result["model"] == "opus"
    assert result["status"] == "idle"


async def test_pre_tool_use():
    await process_hook_event(
        {
            "event_type": "SessionStart",
            "session_id": "sess-2",
            "cwd": "/tmp/test",
        }
    )
    result = await process_hook_event(
        {
            "event_type": "PreToolUse",
            "session_id": "sess-2",
            "tool_name": "Read",
        }
    )
    assert result["status"] == "working"


async def test_post_tool_use():
    await process_hook_event(
        {
            "event_type": "SessionStart",
            "session_id": "sess-3",
            "cwd": "/tmp/test",
        }
    )
    await process_hook_event(
        {
            "event_type": "PreToolUse",
            "session_id": "sess-3",
            "tool_name": "Write",
        }
    )
    result = await process_hook_event(
        {
            "event_type": "PostToolUse",
            "session_id": "sess-3",
            "tool_name": "Write",
        }
    )
    # PostToolUse doesn't change status, just updates activity
    assert result is not None


async def test_stop_event():
    await process_hook_event(
        {
            "event_type": "SessionStart",
            "session_id": "sess-4",
            "cwd": "/tmp/test",
        }
    )
    await process_hook_event(
        {
            "event_type": "PreToolUse",
            "session_id": "sess-4",
        }
    )
    result = await process_hook_event(
        {
            "event_type": "Stop",
            "session_id": "sess-4",
            "cost_usd": 0.05,
            "input_tokens": 1000,
            "output_tokens": 500,
        }
    )
    assert result["status"] == "idle"
    assert result["cost_usd"] == 0.05
    assert result["input_tokens"] == 1000


async def test_session_end():
    await process_hook_event(
        {
            "event_type": "SessionStart",
            "session_id": "sess-5",
            "cwd": "/tmp/test",
        }
    )
    result = await process_hook_event(
        {
            "event_type": "SessionEnd",
            "session_id": "sess-5",
        }
    )
    assert result["status"] == "completed"
    assert result["ended_at"] is not None


async def test_notification_event():
    await process_hook_event(
        {
            "event_type": "SessionStart",
            "session_id": "sess-6",
            "cwd": "/tmp/test",
        }
    )
    result = await process_hook_event(
        {
            "event_type": "Notification",
            "session_id": "sess-6",
            "message": "Waiting for permission to write file",
        }
    )
    assert result["status"] == "waiting"
    # Notification should NOT overwrite task_description
    assert result.get("task_description") is None


async def test_subagent_events():
    await process_hook_event(
        {
            "event_type": "SessionStart",
            "session_id": "sess-7",
            "cwd": "/tmp/test",
        }
    )
    result = await process_hook_event(
        {
            "event_type": "SubagentStart",
            "session_id": "sess-7",
        }
    )
    assert result is not None

    result = await process_hook_event(
        {
            "event_type": "SubagentStop",
            "session_id": "sess-7",
        }
    )
    assert result is not None


async def test_unknown_event_creates_session():
    result = await process_hook_event(
        {
            "event_type": "SomeNewEvent",
            "session_id": "sess-8",
        }
    )
    assert result is not None
    session = await db.get_session("sess-8")
    assert session is not None


async def test_missing_session_id():
    result = await process_hook_event(
        {
            "event_type": "SessionStart",
        }
    )
    assert result is None


async def test_events_recorded():
    await process_hook_event(
        {
            "event_type": "SessionStart",
            "session_id": "sess-9",
            "cwd": "/tmp/test",
        }
    )
    await process_hook_event(
        {
            "event_type": "PreToolUse",
            "session_id": "sess-9",
            "tool_name": "Bash",
        }
    )
    events = await db.get_session_events("sess-9")
    assert len(events) == 2
    types = [e["event_type"] for e in events]
    assert "SessionStart" in types
    assert "PreToolUse" in types


async def test_stale_session_detection():
    """Test that sessions with old activity are marked stale."""
    await db.create_session("stale-1")
    old_time = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    await db.update_session("stale-1", status="idle", last_activity_at=old_time)

    # Manually run the check logic
    sessions = await db.get_all_active_sessions()
    now = datetime.now(UTC)
    for session in sessions:
        if session["status"] in ("completed", "stale"):
            continue
        last = session.get("last_activity_at")
        if last:
            last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=UTC)
            diff = (now - last_dt).total_seconds()
            if diff > 300:
                await db.update_session(session["id"], status="stale")

    session = await db.get_session("stale-1")
    assert session["status"] == "stale"


async def test_stop_with_context_tracking():
    await process_hook_event(
        {
            "event_type": "SessionStart",
            "session_id": "ctx-1",
            "cwd": "/tmp/test",
        }
    )
    result = await process_hook_event(
        {
            "event_type": "Stop",
            "session_id": "ctx-1",
            "context_tokens": 50000,
            "context_max": 200000,
        }
    )
    assert result["context_tokens"] == 50000
    assert result["context_usage_percent"] == 25.0


async def test_git_branch_extraction():
    """Test that git branch is extracted on SessionStart."""
    with patch("server.hooks._extract_git_branch", return_value="feature/test"):
        result = await process_hook_event(
            {
                "event_type": "SessionStart",
                "session_id": "git-1",
                "cwd": "/tmp/test",
            }
        )
        assert result["git_branch"] == "feature/test"


async def test_notify_update_with_callback():
    """Test that _notify_update calls the callback."""
    from server.hooks import _notify_update, set_update_callback

    callback = AsyncMock()
    set_update_callback(callback)
    try:
        await _notify_update({"id": "test"})
        callback.assert_called_once_with({"id": "test"})
    finally:
        set_update_callback(None)


async def test_notify_update_without_callback():
    """Test that _notify_update is no-op without callback."""
    from server.hooks import _notify_update, set_update_callback

    set_update_callback(None)
    await _notify_update({"id": "test"})  # Should not raise


def test_read_effort_level_success():
    """Test reading effort level from settings file."""
    from server.hooks import _read_effort_level

    with (
        patch(
            "builtins.open",
            MagicMock(
                return_value=MagicMock(
                    __enter__=lambda s: s,
                    __exit__=MagicMock(return_value=False),
                    read=MagicMock(return_value='{"effortLevel": "high"}'),
                )
            ),
        ),
        patch("json.load", return_value={"effortLevel": "high"}),
    ):
        assert _read_effort_level() == "high"


def test_read_effort_level_file_not_found():
    """Test reading effort level when settings file doesn't exist."""
    from server.hooks import _read_effort_level

    with patch("builtins.open", side_effect=FileNotFoundError):
        assert _read_effort_level() is None


def test_extract_git_branch_success():
    """Test git branch extraction from working directory."""
    from server.hooks import _extract_git_branch

    mock_result = MagicMock(returncode=0, stdout="feature/my-branch\n")
    with patch("subprocess.run", return_value=mock_result):
        assert _extract_git_branch("/tmp/test") == "feature/my-branch"


def test_extract_git_branch_failure():
    """Test git branch extraction when git fails."""
    from server.hooks import _extract_git_branch

    mock_result = MagicMock(returncode=1, stdout="")
    with patch("subprocess.run", return_value=mock_result):
        assert _extract_git_branch("/tmp/test") is None


def test_extract_git_branch_no_dir():
    """Test git branch extraction with no directory."""
    from server.hooks import _extract_git_branch

    assert _extract_git_branch(None) is None


def test_extract_git_branch_timeout():
    """Test git branch extraction when subprocess times out."""
    from server.hooks import _extract_git_branch

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 5)):
        assert _extract_git_branch("/tmp/test") is None


async def test_session_start_with_effort_level():
    """Test that effort level is read on new session creation."""
    with patch("server.hooks._extract_git_branch", return_value=None):
        with patch("server.hooks._read_effort_level", return_value="low"):
            result = await process_hook_event(
                {
                    "event_type": "SessionStart",
                    "session_id": "effort-1",
                    "cwd": "/tmp/test",
                }
            )
            assert result["effort_level"] == "low"


async def test_cache_tokens_in_stop():
    """Test that cache_tokens are recorded in Stop events."""
    await process_hook_event(
        {
            "event_type": "SessionStart",
            "session_id": "cache-1",
            "cwd": "/tmp/test",
        }
    )
    result = await process_hook_event(
        {
            "event_type": "Stop",
            "session_id": "cache-1",
            "cache_tokens": 5000,
        }
    )
    assert result["cache_tokens"] == 5000


async def test_process_hook_broadcasts_update():
    """Test that process_hook_event calls _notify_update."""
    from server.hooks import set_update_callback

    callback = AsyncMock()
    set_update_callback(callback)
    try:
        with patch("server.hooks._extract_git_branch", return_value=None):
            await process_hook_event(
                {
                    "event_type": "SessionStart",
                    "session_id": "broadcast-1",
                    "cwd": "/tmp/test",
                }
            )
            assert callback.call_count >= 1
    finally:
        set_update_callback(None)


async def test_project_path_update_on_existing_session():
    """Test that project info is set from cwd when session lacks it."""
    with patch("server.hooks._extract_git_branch", return_value=None):
        await db.create_session("proj-update-1")
        result = await process_hook_event(
            {
                "event_type": "PreToolUse",
                "session_id": "proj-update-1",
                "cwd": "/home/user/myproject",
            }
        )
        assert result["project_name"] == "myproject"
        assert result["project_path"] == "/home/user/myproject"


def test_start_and_stop_stale_checker():
    """Test starting and stopping the stale checker."""
    import server.hooks as hooks_mod
    from server.hooks import start_stale_checker, stop_stale_checker

    with patch("asyncio.create_task") as mock_create:
        mock_task = MagicMock()
        mock_task.done.return_value = False
        mock_create.return_value = mock_task
        start_stale_checker()
        mock_create.assert_called_once()

        hooks_mod._stale_checker_task = mock_task
        stop_stale_checker()
        mock_task.cancel.assert_called_once()
        assert hooks_mod._stale_checker_task is None


async def test_event_field_alias():
    """Test that 'event' field works as alias for 'event_type'."""
    with patch("server.hooks._extract_git_branch", return_value=None):
        result = await process_hook_event(
            {
                "event": "SessionStart",
                "session_id": "alias-1",
                "cwd": "/tmp/test",
            }
        )
        assert result is not None
        assert result["status"] == "idle"


async def test_check_stale_sessions_marks_stale():
    """Test _check_stale_sessions marks old sessions as stale."""
    import server.hooks as hooks_mod
    from server.hooks import _check_stale_sessions

    # Create a session with old activity
    old_time = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    await db.create_session("stale-check-1")
    await db.update_session("stale-check-1", status="idle", last_activity_at=old_time)

    # Create a recent session
    recent_time = datetime.now(UTC).isoformat()
    await db.create_session("stale-check-2")
    await db.update_session("stale-check-2", status="working", last_activity_at=recent_time)

    # Patch sleep to only run once then cancel
    call_count = 0

    async def mock_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise asyncio.CancelledError()

    with patch("asyncio.sleep", side_effect=mock_sleep), patch.object(hooks_mod, "_on_session_update", new=None):
        try:
            await _check_stale_sessions()
        except asyncio.CancelledError:
            pass

    s1 = await db.get_session("stale-check-1")
    assert s1["status"] == "stale"

    s2 = await db.get_session("stale-check-2")
    assert s2["status"] == "working"


async def test_check_stale_sessions_skips_completed():
    """_check_stale_sessions should skip completed/stale sessions."""
    from server.hooks import _check_stale_sessions

    old_time = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    await db.create_session("stale-skip-1")
    await db.update_session("stale-skip-1", status="completed", last_activity_at=old_time)

    call_count = 0

    async def mock_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise asyncio.CancelledError()

    with patch("asyncio.sleep", side_effect=mock_sleep):
        try:
            await _check_stale_sessions()
        except asyncio.CancelledError:
            pass

    s = await db.get_session("stale-skip-1")
    assert s["status"] == "completed"


async def test_check_stale_sessions_no_last_activity():
    """_check_stale_sessions skips sessions without last_activity_at."""
    from server.hooks import _check_stale_sessions

    await db.create_session("stale-noact-1")
    await db.update_session("stale-noact-1", status="idle")

    call_count = 0

    async def mock_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise asyncio.CancelledError()

    with patch("asyncio.sleep", side_effect=mock_sleep):
        try:
            await _check_stale_sessions()
        except asyncio.CancelledError:
            pass

    s = await db.get_session("stale-noact-1")
    assert s["status"] == "idle"


async def test_check_stale_sessions_handles_exception():
    """_check_stale_sessions handles general exceptions gracefully."""
    from server.hooks import _check_stale_sessions

    call_count = 0

    async def mock_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return  # Allow first iteration
        raise asyncio.CancelledError()

    with patch("asyncio.sleep", side_effect=mock_sleep):
        with patch("server.db.get_all_active_sessions", side_effect=Exception("db error")):
            try:
                await _check_stale_sessions()
            except asyncio.CancelledError:
                pass  # Expected


async def test_project_path_with_git_branch_on_existing():
    """Test git branch is extracted when project path is set on existing session."""
    with patch("server.hooks._extract_git_branch", return_value="main"):
        await db.create_session("branch-update-1")
        result = await process_hook_event(
            {
                "event_type": "PreToolUse",
                "session_id": "branch-update-1",
                "cwd": "/home/user/myproject",
            }
        )
        assert result["git_branch"] == "main"


async def test_check_stale_sessions_timezone_naive():
    """Test stale checker handles timezone-naive timestamps."""
    from server.hooks import _check_stale_sessions

    # Simulate a timezone-naive ISO timestamp without Z
    old_naive = (datetime.now(UTC) - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%S")
    await db.create_session("stale-naive-1")
    await db.update_session("stale-naive-1", status="idle", last_activity_at=old_naive)

    call_count = 0

    async def mock_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise asyncio.CancelledError()

    with patch("asyncio.sleep", side_effect=mock_sleep):
        try:
            await _check_stale_sessions()
        except asyncio.CancelledError:
            pass

    s = await db.get_session("stale-naive-1")
    assert s["status"] == "stale"


async def test_check_stale_sessions_invalid_timestamp():
    """Test stale checker handles invalid timestamps gracefully."""
    from server.hooks import _check_stale_sessions

    await db.create_session("stale-bad-ts")
    await db.update_session("stale-bad-ts", status="idle", last_activity_at="not-a-date")

    call_count = 0

    async def mock_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise asyncio.CancelledError()

    with patch("asyncio.sleep", side_effect=mock_sleep):
        try:
            await _check_stale_sessions()
        except asyncio.CancelledError:
            pass

    # Should not crash, session stays idle
    s = await db.get_session("stale-bad-ts")
    assert s["status"] == "idle"


async def test_check_stale_notifies_on_update():
    """Test that stale checker notifies on session update."""
    from server.hooks import _check_stale_sessions, set_update_callback

    old_time = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    await db.create_session("stale-notify-1")
    await db.update_session("stale-notify-1", status="idle", last_activity_at=old_time)

    callback = AsyncMock()
    set_update_callback(callback)

    call_count = 0

    async def mock_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise asyncio.CancelledError()

    try:
        with patch("asyncio.sleep", side_effect=mock_sleep):
            try:
                await _check_stale_sessions()
            except asyncio.CancelledError:
                pass

        assert callback.call_count >= 1
    finally:
        set_update_callback(None)
