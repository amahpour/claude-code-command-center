"""Hook event processing — handles events from Claude Code hooks."""

import asyncio
import logging
import subprocess
from datetime import datetime, timezone

from server import db

logger = logging.getLogger(__name__)

# Track the stale-checker task
_stale_checker_task: asyncio.Task | None = None

# Callbacks for broadcasting updates
_on_session_update = None


def set_update_callback(callback):
    """Set a callback to be called when a session is updated."""
    global _on_session_update
    _on_session_update = callback


async def _notify_update(session: dict):
    """Notify listeners of a session update."""
    if _on_session_update:
        await _on_session_update(session)


def _extract_git_branch(working_dir: str | None) -> str | None:
    """Extract the current git branch from a working directory."""
    if not working_dir:
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


async def process_hook_event(event_data: dict) -> dict | None:
    """Process a hook event and update the database accordingly.

    Returns the updated session dict, or None if the event was ignored.
    """
    event_type = event_data.get("event_type") or event_data.get("event", "")
    session_id = event_data.get("session_id", "")
    tool_name = event_data.get("tool_name")
    payload = event_data

    if not session_id:
        logger.warning("Hook event missing session_id: %s", event_type)
        return None

    now = datetime.now(timezone.utc).isoformat()
    session = await db.get_session(session_id)

    # Ensure session exists before recording event (FK constraint)
    if session is None:
        if event_type == "SessionStart":
            project_path = event_data.get("cwd") or event_data.get("project_path")
            session = await db.create_session(
                session_id,
                project_path=project_path,
                model=event_data.get("model"),
            )
        else:
            session = await db.create_session(session_id)

    # Record the event (session now guaranteed to exist)
    await db.add_event(session_id, event_type, tool_name=tool_name, payload=payload)

    # Session is guaranteed to exist at this point.
    # Now apply event-specific updates.

    if event_type == "SessionStart":
        project_path = event_data.get("cwd") or event_data.get("project_path")
        model = event_data.get("model")
        git_branch = _extract_git_branch(project_path)
        updates = {"last_activity_at": now, "status": "idle"}
        if project_path:
            updates["project_path"] = project_path
            updates["project_name"] = project_path.rsplit("/", 1)[-1] if "/" in project_path else project_path
        if model:
            updates["model"] = model
        if git_branch:
            updates["git_branch"] = git_branch
        session = await db.update_session(session_id, **updates)

    elif event_type == "PreToolUse":
        session = await db.update_session(
            session_id, status="working", last_activity_at=now
        )

    elif event_type == "PostToolUse":
        session = await db.update_session(session_id, last_activity_at=now)

    elif event_type == "Stop":
        updates: dict = {"status": "idle", "last_activity_at": now}
        if "cost_usd" in event_data:
            updates["cost_usd"] = event_data["cost_usd"]
        if "input_tokens" in event_data:
            updates["input_tokens"] = event_data["input_tokens"]
        if "output_tokens" in event_data:
            updates["output_tokens"] = event_data["output_tokens"]
        if "cache_tokens" in event_data:
            updates["cache_tokens"] = event_data["cache_tokens"]
        if "context_tokens" in event_data:
            updates["context_tokens"] = event_data["context_tokens"]
            max_ctx = event_data.get("context_max", 200000)
            updates["context_max"] = max_ctx
            if max_ctx > 0:
                updates["context_usage_percent"] = (event_data["context_tokens"] / max_ctx) * 100
        session = await db.update_session(session_id, **updates)

    elif event_type == "SubagentStart":
        session = await db.update_session(session_id, last_activity_at=now)

    elif event_type == "SubagentStop":
        session = await db.update_session(session_id, last_activity_at=now)

    elif event_type == "Notification":
        updates = {"last_activity_at": now, "status": "waiting"}
        message = event_data.get("message", "")
        if message:
            updates["task_description"] = message
        session = await db.update_session(session_id, **updates)

    elif event_type == "SessionEnd":
        session = await db.update_session(
            session_id, status="completed", ended_at=now, last_activity_at=now
        )

    else:
        session = await db.update_session(session_id, last_activity_at=now)

    if session:
        await _notify_update(session)

    return session


async def _check_stale_sessions():
    """Background task that marks inactive sessions as stale."""
    while True:
        try:
            await asyncio.sleep(60)
            sessions = await db.get_all_active_sessions()
            now = datetime.now(timezone.utc)
            for session in sessions:
                if session["status"] in ("completed", "stale"):
                    continue
                last_activity = session.get("last_activity_at")
                if not last_activity:
                    continue
                try:
                    last_dt = datetime.fromisoformat(last_activity.replace("Z", "+00:00"))
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                    diff = (now - last_dt).total_seconds()
                    if diff > 300:  # 5 minutes
                        updated = await db.update_session(session["id"], status="stale")
                        if updated:
                            await _notify_update(updated)
                except (ValueError, TypeError):
                    pass
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Error in stale session checker")


def start_stale_checker():
    """Start the background stale session checker."""
    global _stale_checker_task
    if _stale_checker_task is None or _stale_checker_task.done():
        _stale_checker_task = asyncio.create_task(_check_stale_sessions())


def stop_stale_checker():
    """Stop the background stale session checker."""
    global _stale_checker_task
    if _stale_checker_task and not _stale_checker_task.done():
        _stale_checker_task.cancel()
        _stale_checker_task = None
