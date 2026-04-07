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
    # Notification message should be stored in task_description
    assert result["task_description"] == "Waiting for permission to write file"


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


async def test_subagent_start_creates_linked_session():
    """SubagentStart with agent_id creates a subagent session linked to parent."""
    await process_hook_event(
        {
            "event_type": "SessionStart",
            "session_id": "parent-link-1",
            "cwd": "/tmp/test",
        }
    )
    result = await process_hook_event(
        {
            "event_type": "SubagentStart",
            "session_id": "parent-link-1",
            "agent_id": "sub-link-1",
            "agent_type": "codegen",
        }
    )
    assert result is not None

    subagent = await db.get_session("sub-link-1")
    assert subagent is not None
    assert subagent["parent_session_id"] == "parent-link-1"
    assert subagent["agent_type"] == "codegen"
    assert subagent["status"] == "working"


async def test_subagent_stop_completes_subagent():
    """SubagentStop marks the subagent as completed and sets task_description."""
    await process_hook_event(
        {
            "event_type": "SessionStart",
            "session_id": "parent-stop-1",
            "cwd": "/tmp/test",
        }
    )
    await process_hook_event(
        {
            "event_type": "SubagentStart",
            "session_id": "parent-stop-1",
            "agent_id": "sub-stop-1",
            "agent_type": "research",
        }
    )
    await process_hook_event(
        {
            "event_type": "SubagentStop",
            "session_id": "parent-stop-1",
            "agent_id": "sub-stop-1",
            "last_assistant_message": "Finished analyzing the codebase",
        }
    )

    subagent = await db.get_session("sub-stop-1")
    assert subagent is not None
    assert subagent["status"] == "completed"
    assert subagent["ended_at"] is not None
    assert subagent["task_description"] == "Finished analyzing the codebase"


async def test_subagent_start_broadcasts_parent_with_subagents():
    """SubagentStart notifies callback with parent session including subagents array."""
    from server.hooks import set_update_callback

    callback = AsyncMock()
    set_update_callback(callback)

    try:
        await process_hook_event(
            {
                "event_type": "SessionStart",
                "session_id": "parent-bc-1",
                "cwd": "/tmp/test",
            }
        )
        callback.reset_mock()

        await process_hook_event(
            {
                "event_type": "SubagentStart",
                "session_id": "parent-bc-1",
                "agent_id": "sub-bc-1",
                "agent_type": "codegen",
            }
        )

        # The callback should have been called with the parent session
        assert callback.call_count >= 1
        # Find the call that includes subagents (the parent broadcast)
        parent_broadcast = None
        for call in callback.call_args_list:
            session_arg = call[0][0]
            if session_arg.get("id") == "parent-bc-1" and "subagents" in session_arg:
                parent_broadcast = session_arg
                break
        assert parent_broadcast is not None
        assert len(parent_broadcast["subagents"]) == 1
        assert parent_broadcast["subagents"][0]["id"] == "sub-bc-1"
    finally:
        set_update_callback(None)


# --- Issue #1: Notification hook pipeline and hardening tests ---


async def test_notification_stores_message_in_task_description():
    """Notification event message should be saved to task_description."""
    await process_hook_event({"event_type": "SessionStart", "session_id": "notif-msg-1", "cwd": "/tmp/test"})
    result = await process_hook_event(
        {
            "event_type": "Notification",
            "session_id": "notif-msg-1",
            "message": "Claude wants to run: rm -rf /tmp/foo",
        }
    )
    assert result["status"] == "waiting"
    assert result["task_description"] == "Claude wants to run: rm -rf /tmp/foo"


async def test_notification_empty_message_does_not_overwrite():
    """Notification with empty message should not overwrite existing task_description."""
    await db.create_session("notif-empty-1", task_description="Initial task")
    result = await process_hook_event(
        {
            "event_type": "Notification",
            "session_id": "notif-empty-1",
            "message": "",
        }
    )
    assert result["status"] == "waiting"
    assert result["task_description"] == "Initial task"


async def test_notification_missing_message_field():
    """Notification without message field should not overwrite existing task_description."""
    await db.create_session("notif-nomsg-1", task_description="Existing desc")
    result = await process_hook_event(
        {
            "event_type": "Notification",
            "session_id": "notif-nomsg-1",
        }
    )
    assert result["status"] == "waiting"
    assert result["task_description"] == "Existing desc"


async def test_notification_creates_session_if_missing():
    """Notification for unknown session_id should auto-create the session."""
    result = await process_hook_event(
        {
            "event_type": "Notification",
            "session_id": "notif-new-1",
            "message": "Permission needed",
        }
    )
    assert result is not None
    assert result["status"] == "waiting"
    assert result["task_description"] == "Permission needed"
    session = await db.get_session("notif-new-1")
    assert session is not None


async def test_notification_broadcasts_update():
    """Notification event should trigger the update callback."""
    from server.hooks import set_update_callback

    callback = AsyncMock()
    set_update_callback(callback)
    try:
        await process_hook_event({"event_type": "SessionStart", "session_id": "notif-bc-1", "cwd": "/tmp/test"})
        callback.reset_mock()
        await process_hook_event(
            {
                "event_type": "Notification",
                "session_id": "notif-bc-1",
                "message": "Approve write?",
            }
        )
        assert callback.call_count == 1
        broadcast_session = callback.call_args[0][0]
        assert broadcast_session["status"] == "waiting"
        assert broadcast_session["task_description"] == "Approve write?"
    finally:
        set_update_callback(None)


# --- Full status lifecycle transition tests ---


async def test_full_lifecycle_idle_working_waiting_working_idle():
    """Test the complete lifecycle: idle → working → waiting → working → idle."""
    with patch("server.hooks._extract_git_branch", return_value=None):
        sid = "lifecycle-1"
        r = await process_hook_event({"event_type": "SessionStart", "session_id": sid, "cwd": "/tmp/test"})
        assert r["status"] == "idle"

        r = await process_hook_event({"event_type": "PreToolUse", "session_id": sid, "tool_name": "Write"})
        assert r["status"] == "working"

        r = await process_hook_event({"event_type": "Notification", "session_id": sid, "message": "Approve?"})
        assert r["status"] == "waiting"

        r = await process_hook_event({"event_type": "PreToolUse", "session_id": sid, "tool_name": "Write"})
        assert r["status"] == "working"

        r = await process_hook_event({"event_type": "Stop", "session_id": sid, "cost_usd": 0.01})
        assert r["status"] == "idle"


async def test_lifecycle_waiting_to_stop():
    """Test transition: waiting → idle via Stop (user approves and session completes)."""
    sid = "lifecycle-2"
    await process_hook_event({"event_type": "SessionStart", "session_id": sid, "cwd": "/tmp/test"})
    await process_hook_event({"event_type": "Notification", "session_id": sid, "message": "Approve?"})
    r = await process_hook_event({"event_type": "Stop", "session_id": sid})
    assert r["status"] == "idle"


async def test_lifecycle_waiting_to_session_end():
    """Test transition: waiting → completed via SessionEnd."""
    sid = "lifecycle-3"
    await process_hook_event({"event_type": "SessionStart", "session_id": sid, "cwd": "/tmp/test"})
    await process_hook_event({"event_type": "Notification", "session_id": sid, "message": "Approve?"})
    r = await process_hook_event({"event_type": "SessionEnd", "session_id": sid})
    assert r["status"] == "completed"


async def test_rapid_successive_notifications():
    """Rapid Notification events should each update task_description."""
    sid = "rapid-notif-1"
    await process_hook_event({"event_type": "SessionStart", "session_id": sid, "cwd": "/tmp/test"})
    for i in range(5):
        r = await process_hook_event(
            {
                "event_type": "Notification",
                "session_id": sid,
                "message": f"Permission #{i}",
            }
        )
        assert r["status"] == "waiting"
        assert r["task_description"] == f"Permission #{i}"


async def test_notification_followed_by_immediate_stop():
    """Race condition: Notification then Stop in quick succession."""
    sid = "race-1"
    await process_hook_event({"event_type": "SessionStart", "session_id": sid, "cwd": "/tmp/test"})
    await process_hook_event({"event_type": "Notification", "session_id": sid, "message": "Approve?"})
    # Stop arrives immediately after
    r = await process_hook_event({"event_type": "Stop", "session_id": sid, "cost_usd": 0.01})
    assert r["status"] == "idle"


# --- Stale checker: waiting-specific handling tests ---


async def test_stale_checker_waiting_session_uses_longer_timeout():
    """Waiting sessions should NOT be marked stale at 5 min (only at 10 min)."""
    from server.hooks import _check_stale_sessions

    # 7 minutes old — past normal 5-min threshold but within 10-min waiting threshold
    old_time = (datetime.now(UTC) - timedelta(minutes=7)).isoformat()
    await db.create_session("stale-wait-1")
    await db.update_session("stale-wait-1", status="waiting", last_activity_at=old_time)

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

    s = await db.get_session("stale-wait-1")
    assert s["status"] == "waiting"  # Should NOT be stale yet


async def test_stale_checker_waiting_session_stale_after_10_min():
    """Waiting sessions should be marked stale after 10 minutes."""
    import server.hooks as hooks_mod
    from server.hooks import _check_stale_sessions

    # 12 minutes old — past 10-min waiting threshold
    old_time = (datetime.now(UTC) - timedelta(minutes=12)).isoformat()
    await db.create_session("stale-wait-2")
    await db.update_session("stale-wait-2", status="waiting", last_activity_at=old_time)

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

    s = await db.get_session("stale-wait-2")
    assert s["status"] == "stale"


async def test_stale_checker_non_waiting_still_5_min():
    """Non-waiting sessions should still use the 5-min threshold."""
    import server.hooks as hooks_mod
    from server.hooks import _check_stale_sessions

    old_time = (datetime.now(UTC) - timedelta(minutes=7)).isoformat()
    await db.create_session("stale-idle-1")
    await db.update_session("stale-idle-1", status="idle", last_activity_at=old_time)

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

    s = await db.get_session("stale-idle-1")
    assert s["status"] == "stale"  # 7 min > 5 min threshold


async def test_stale_checker_logs_warning_for_long_waiting():
    """Stale checker should log a warning when a waiting session times out."""
    import server.hooks as hooks_mod
    from server.hooks import _check_stale_sessions

    old_time = (datetime.now(UTC) - timedelta(minutes=12)).isoformat()
    await db.create_session("stale-warn-1")
    await db.update_session("stale-warn-1", status="waiting", last_activity_at=old_time)

    call_count = 0

    async def mock_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise asyncio.CancelledError()

    with (
        patch("asyncio.sleep", side_effect=mock_sleep),
        patch.object(hooks_mod, "_on_session_update", new=None),
        patch("server.hooks.logger") as mock_logger,
    ):
        try:
            await _check_stale_sessions()
        except asyncio.CancelledError:
            pass

    mock_logger.warning.assert_called_once()
    assert "stale-warn-1" in mock_logger.warning.call_args[0][1]


async def test_check_stale_subagents_marks_completed():
    """_check_stale_subagents should mark inactive subagents as completed."""
    from server.hooks import _check_stale_subagents

    old_time = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    await db.create_session("parent-sub-stale")
    await db.update_session("parent-sub-stale", status="working")
    await db.create_session("sub-stale-1")
    await db.update_session(
        "sub-stale-1",
        parent_session_id="parent-sub-stale",
        status="working",
        last_activity_at=old_time,
    )

    with patch("server.hooks._on_session_update", new=None):
        await _check_stale_subagents(datetime.now(UTC))

    sub = await db.get_session("sub-stale-1")
    assert sub["status"] == "completed"
    assert sub["ended_at"] is not None


async def test_check_stale_subagents_skips_recent():
    """_check_stale_subagents should not touch recently active subagents."""
    from server.hooks import _check_stale_subagents

    recent_time = datetime.now(UTC).isoformat()
    await db.create_session("parent-sub-recent")
    await db.create_session("sub-recent-1")
    await db.update_session(
        "sub-recent-1",
        parent_session_id="parent-sub-recent",
        status="working",
        last_activity_at=recent_time,
    )

    await _check_stale_subagents(datetime.now(UTC))

    sub = await db.get_session("sub-recent-1")
    assert sub["status"] == "working"


async def test_check_stale_subagents_broadcasts_parent():
    """_check_stale_subagents should broadcast parent update when subagent completes."""
    from server.hooks import _check_stale_subagents, set_update_callback

    old_time = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    await db.create_session("parent-sub-bc")
    await db.update_session("parent-sub-bc", status="working")
    await db.create_session("sub-bc-1")
    await db.update_session(
        "sub-bc-1",
        parent_session_id="parent-sub-bc",
        status="working",
        last_activity_at=old_time,
    )

    callback = AsyncMock()
    set_update_callback(callback)

    try:
        await _check_stale_subagents(datetime.now(UTC))
        assert callback.call_count >= 1
        # The parent session should have been broadcast
        updated_session = callback.call_args[0][0]
        assert updated_session["id"] == "parent-sub-bc"
        assert "subagents" in updated_session
    finally:
        set_update_callback(None)


async def test_check_stale_subagents_no_last_activity():
    """_check_stale_subagents should skip subagents without last_activity_at."""
    from server.hooks import _check_stale_subagents

    await db.create_session("parent-sub-nola")
    await db.create_session("sub-nola-1")
    await db.update_session(
        "sub-nola-1",
        parent_session_id="parent-sub-nola",
        status="working",
        last_activity_at=None,
    )

    # Verify last_activity_at is actually None
    sub_before = await db.get_session("sub-nola-1")
    assert sub_before["last_activity_at"] is None

    await _check_stale_subagents(datetime.now(UTC))

    sub = await db.get_session("sub-nola-1")
    assert sub["status"] == "working"


async def test_check_stale_subagents_timezone_naive():
    """_check_stale_subagents should handle timezone-naive timestamps."""
    from server.hooks import _check_stale_subagents

    # Use a naive timestamp (no timezone info) that is old enough to be stale
    old_naive = (datetime.now(UTC) - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%S")
    await db.create_session("parent-sub-naive")
    await db.update_session("parent-sub-naive", status="working")
    await db.create_session("sub-naive-1")
    await db.update_session(
        "sub-naive-1",
        parent_session_id="parent-sub-naive",
        status="working",
        last_activity_at=old_naive,
    )

    with patch("server.hooks._on_session_update", new=None):
        await _check_stale_subagents(datetime.now(UTC))

    sub = await db.get_session("sub-naive-1")
    assert sub["status"] == "completed"


async def test_check_stale_subagents_invalid_timestamp():
    """_check_stale_subagents should handle invalid timestamps gracefully."""
    from server.hooks import _check_stale_subagents

    await db.create_session("parent-sub-bad")
    await db.create_session("sub-bad-ts")
    await db.update_session(
        "sub-bad-ts",
        parent_session_id="parent-sub-bad",
        status="working",
        last_activity_at="not-a-timestamp",
    )

    # Should not crash
    await _check_stale_subagents(datetime.now(UTC))

    sub = await db.get_session("sub-bad-ts")
    assert sub["status"] == "working"


async def test_check_stale_sessions_skips_working_subagent():
    """_check_stale_sessions should not mark a session stale if it has working subagents."""
    import server.hooks as hooks_mod
    from server.hooks import _check_stale_sessions

    old_time = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    await db.create_session("parent-with-sub")
    await db.update_session("parent-with-sub", status="working", last_activity_at=old_time)
    await db.create_session("sub-active-1")
    await db.update_session(
        "sub-active-1",
        parent_session_id="parent-with-sub",
        status="working",
        last_activity_at=datetime.now(UTC).isoformat(),
    )

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

    parent = await db.get_session("parent-with-sub")
    assert parent["status"] == "working"
