"""Hook event processing — handles events from Claude Code hooks."""

import asyncio
import json
import logging
import os
import subprocess
from datetime import UTC, datetime

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


def _read_effort_level() -> str | None:
    """Read the effort level from Claude Code settings."""
    try:
        settings_path = os.path.expanduser("~/.claude/settings.json")
        with open(settings_path) as f:
            settings = json.load(f)
        return settings.get("effortLevel")
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


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

    now = datetime.now(UTC).isoformat()
    project_path = event_data.get("cwd") or event_data.get("project_path")
    session = await db.get_session(session_id)

    # Ensure session exists before recording event (FK constraint)
    if session is None:
        session = await db.create_session(
            session_id,
            project_path=project_path,
            model=event_data.get("model"),
        )
        # Set effort level from settings on first creation
        effort = _read_effort_level()
        if effort:
            session = await db.update_session(session_id, effort_level=effort)

    # Record the event (session now guaranteed to exist)
    await db.add_event(session_id, event_type, tool_name=tool_name, payload=payload)

    # Always update project info from cwd if we have it and session is missing it
    base_updates: dict = {"last_activity_at": now}
    if project_path and session and not session.get("project_name"):
        base_updates["project_path"] = project_path
        base_updates["project_name"] = project_path.rsplit("/", 1)[-1] if "/" in project_path else project_path
        git_branch = _extract_git_branch(project_path)
        if git_branch:
            base_updates["git_branch"] = git_branch

    # Apply event-specific updates on top of base updates

    if event_type == "SessionStart":
        model = event_data.get("model")
        git_branch = _extract_git_branch(project_path)
        base_updates["status"] = "idle"
        if project_path:
            base_updates["project_path"] = project_path
            base_updates["project_name"] = project_path.rsplit("/", 1)[-1] if "/" in project_path else project_path
        if model:
            base_updates["model"] = model
        if git_branch:
            base_updates["git_branch"] = git_branch
        session = await db.update_session(session_id, **base_updates)

    elif event_type == "PreToolUse":
        base_updates["status"] = "working"
        session = await db.update_session(session_id, **base_updates)

    elif event_type == "PostToolUse":
        session = await db.update_session(session_id, **base_updates)

    elif event_type == "Stop":
        base_updates["status"] = "idle"
        if "cost_usd" in event_data:
            base_updates["cost_usd"] = event_data["cost_usd"]
        if "input_tokens" in event_data:
            base_updates["input_tokens"] = event_data["input_tokens"]
        if "output_tokens" in event_data:
            base_updates["output_tokens"] = event_data["output_tokens"]
        if "cache_tokens" in event_data:
            base_updates["cache_tokens"] = event_data["cache_tokens"]
        if "context_tokens" in event_data:
            base_updates["context_tokens"] = event_data["context_tokens"]
            max_ctx = event_data.get("context_max", 200000)
            base_updates["context_max"] = max_ctx
            if max_ctx > 0:
                base_updates["context_usage_percent"] = (event_data["context_tokens"] / max_ctx) * 100
        session = await db.update_session(session_id, **base_updates)

    elif event_type == "SubagentStart":
        # The session_id in the event is the PARENT session ID
        # The agent_id is the subagent's own ID
        agent_id = event_data.get("agent_id", "")
        agent_type = event_data.get("agent_type", "")
        if agent_id:
            # Create or update the subagent session, linked to parent
            subagent = await db.get_session(agent_id)
            if subagent is None:
                subagent = await db.create_session(
                    agent_id,
                    project_path=project_path,
                )
            await db.update_session(
                agent_id,
                parent_session_id=session_id,
                agent_type=agent_type,
                status="working",
                last_activity_at=now,
            )
        # Also update the parent session's last_activity_at
        session = await db.update_session(session_id, **base_updates)

    elif event_type == "SubagentStop":
        agent_id = event_data.get("agent_id", "")
        if agent_id:
            subagent_updates = {
                "status": "completed",
                "ended_at": now,
                "last_activity_at": now,
            }
            last_msg = event_data.get("last_assistant_message", "")
            if last_msg:
                subagent_updates["task_description"] = last_msg[:200]
            await db.update_session(agent_id, **subagent_updates)
        # Also update the parent session's last_activity_at
        session = await db.update_session(session_id, **base_updates)

    elif event_type == "Notification":
        base_updates["status"] = "waiting"
        message = event_data.get("message", "")
        if message:
            base_updates["task_description"] = message
        session = await db.update_session(session_id, **base_updates)

    elif event_type == "SessionEnd":
        base_updates["status"] = "completed"
        base_updates["ended_at"] = now
        session = await db.update_session(session_id, **base_updates)

    else:
        session = await db.update_session(session_id, **base_updates)

    if session:
        # If this is a subagent event, also notify about the parent session
        # so the frontend can update the nested subagent display
        if event_type in ("SubagentStart", "SubagentStop"):
            parent = await db.get_session(session_id)
            if parent:
                subagents = await db.get_subagents_for_session(session_id)
                parent["subagents"] = subagents
                await _notify_update(parent)
        else:
            await _notify_update(session)

    return session


async def _check_stale_sessions():
    """Background task that marks inactive sessions as stale."""
    while True:
        try:
            await asyncio.sleep(60)
            sessions = await db.get_all_active_sessions()
            now = datetime.now(UTC)
            for session in sessions:
                if session["status"] in ("completed", "stale"):
                    continue
                last_activity = session.get("last_activity_at")
                if not last_activity:
                    continue
                try:
                    last_dt = datetime.fromisoformat(last_activity.replace("Z", "+00:00"))
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=UTC)
                    diff = (now - last_dt).total_seconds()
                    if session["status"] == "waiting":
                        # Waiting sessions use a longer timeout (10 min)
                        # and get logged prominently since they need operator attention
                        if diff > 600:
                            logger.warning(
                                "Session %s has been waiting for operator input for %d seconds",
                                session["id"],
                                int(diff),
                            )
                            updated = await db.update_session(session["id"], status="stale")
                            if updated:
                                await _notify_update(updated)
                    elif diff > 300:  # 5 minutes
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
