"""JSONL file watcher — monitors ~/.claude/projects/ for transcript changes."""

import asyncio
import json
import logging
import os
from pathlib import Path

from server import db

logger = logging.getLogger(__name__)

CLAUDE_PROJECTS_DIR = os.path.expanduser("~/.claude/projects")

# Track last read position per file
_file_positions: dict[str, int] = {}

# Debounce tracking
_debounce_tasks: dict[str, asyncio.Task] = {}
DEBOUNCE_SECONDS = 1.0

_watcher_task: asyncio.Task | None = None


def _parse_jsonl_entry(line: str) -> dict | None:
    """Parse a single JSONL line into a structured entry."""
    try:
        data = json.loads(line.strip())
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(data, dict):
        return None

    entry = {
        "role": None,
        "content": None,
        "token_count": None,
        "timestamp": data.get("timestamp"),
    }

    # Handle different JSONL formats
    msg_type = data.get("type", "")
    message = data.get("message", {})

    if msg_type in ("user", "human"):
        entry["role"] = "user"
        entry["content"] = _extract_content(message.get("content", ""))
    elif msg_type == "assistant":
        entry["role"] = "assistant"
        content = message.get("content", "")
        entry["content"] = _extract_content(content)
    elif msg_type == "tool_result":
        entry["role"] = "tool_result"
        entry["content"] = _extract_content(data.get("content", ""))
    elif msg_type == "result":
        entry["role"] = "assistant"
        result = data.get("result", "")
        entry["content"] = _extract_content(result)
    elif "role" in message:
        entry["role"] = message["role"]
        entry["content"] = _extract_content(message.get("content", ""))

    # Extract token counts if present
    usage = data.get("usage", {})
    if usage:
        entry["token_count"] = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)

    if entry["role"] is None or entry["content"] is None:
        return None

    return entry


def _extract_content(content) -> str | None:
    """Extract text content from various content formats."""
    if isinstance(content, str):
        return content if content.strip() else None

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif item.get("type") == "tool_use":
                    name = item.get("name", "unknown")
                    inp = json.dumps(item.get("input", {}), ensure_ascii=False)
                    # Truncate large inputs
                    if len(inp) > 500:
                        inp = inp[:500] + "..."
                    parts.append(f"[Tool: {name}] {inp}")
                elif item.get("type") == "tool_result":
                    result_content = item.get("content", "")
                    if isinstance(result_content, str):
                        text = result_content
                    else:
                        text = str(result_content)
                    if len(text) > 500:
                        text = text[:500] + "..."
                    parts.append(f"[Result] {text}")
        return "\n".join(parts) if parts else None

    if isinstance(content, dict):
        if content.get("type") == "text":
            return content.get("text")

    return str(content) if content else None


def _session_id_from_path(file_path: str) -> str | None:
    """Extract a session identifier from a JSONL file path.

    Claude Code JSONL files are typically at:
    ~/.claude/projects/<project-hash>/<session-id>.jsonl
    """
    p = Path(file_path)
    if p.suffix != ".jsonl":
        return None
    return p.stem


async def _process_file_changes(file_path: str):
    """Read new lines from a JSONL file and store as transcripts."""
    session_id = _session_id_from_path(file_path)
    if not session_id:
        return

    last_pos = _file_positions.get(file_path, 0)

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(last_pos)
            new_lines = f.readlines()
            _file_positions[file_path] = f.tell()
    except (FileNotFoundError, PermissionError, OSError) as e:
        logger.warning("Cannot read %s: %s", file_path, e)
        return

    if not new_lines:
        return

    # Ensure session exists
    session = await db.get_session(session_id)
    if session is None:
        await db.create_session(session_id, project_path=str(Path(file_path).parent))

    for line in new_lines:
        line = line.strip()
        if not line:
            continue
        entry = _parse_jsonl_entry(line)
        if entry is None:
            continue
        await db.add_transcript(
            session_id=session_id,
            role=entry["role"],
            content=entry["content"],
            source_file=file_path,
            token_count=entry.get("token_count"),
            timestamp=entry.get("timestamp"),
        )


async def _debounced_process(file_path: str):
    """Debounce file processing to avoid excessive reads."""
    await asyncio.sleep(DEBOUNCE_SECONDS)
    await _process_file_changes(file_path)


def _schedule_process(file_path: str):
    """Schedule debounced processing of a file change."""
    if file_path in _debounce_tasks:
        task = _debounce_tasks[file_path]
        if not task.done():
            task.cancel()
    _debounce_tasks[file_path] = asyncio.create_task(_debounced_process(file_path))


async def start_watcher():
    """Start watching for JSONL file changes."""
    global _watcher_task

    projects_dir = CLAUDE_PROJECTS_DIR
    if not os.path.isdir(projects_dir):
        logger.info("Claude projects directory not found: %s", projects_dir)
        return

    try:
        from watchfiles import awatch, Change

        async def _watch():
            try:
                async for changes in awatch(projects_dir, recursive=True):
                    for change_type, path in changes:
                        if path.endswith(".jsonl") and change_type in (Change.added, Change.modified):
                            _schedule_process(path)
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Watcher error")

        _watcher_task = asyncio.create_task(_watch())
        logger.info("Started watching %s", projects_dir)

    except ImportError:
        logger.warning("watchfiles not installed, file watching disabled")


def stop_watcher():
    """Stop the file watcher."""
    global _watcher_task
    if _watcher_task and not _watcher_task.done():
        _watcher_task.cancel()
        _watcher_task = None
    # Cancel any pending debounce tasks
    for task in _debounce_tasks.values():
        if not task.done():
            task.cancel()
    _debounce_tasks.clear()
