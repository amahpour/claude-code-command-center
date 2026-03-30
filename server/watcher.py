"""JSONL file watcher — monitors ~/.claude/projects/ for transcript changes."""

import asyncio
import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from server import db
from server.pr_lookup import find_pr_url

logger = logging.getLogger(__name__)

CLAUDE_PROJECTS_DIR = os.path.expanduser("~/.claude/projects")

# Track last read position per file (persisted to DB across restarts)
_file_positions: dict[str, int] = {}


async def _load_file_positions():
    """Restore file positions from DB on startup."""
    raw = await db.get_setting("file_positions")
    if raw:
        try:
            _file_positions.update(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            pass


async def _save_file_positions():
    """Persist file positions to DB."""
    await db.set_setting("file_positions", json.dumps(_file_positions))


# Debounce tracking
_debounce_tasks: dict[str, asyncio.Task] = {}
DEBOUNCE_SECONDS = 1.0

_watcher_task: asyncio.Task | None = None


def _parse_jsonl_entry(line: str) -> dict | None:
    """Parse a single JSONL line into a structured entry.

    Handles the real Claude Code JSONL format:
    - type: "user" — user messages, content is a string or in nested structure
    - type: "assistant" — assistant messages with message.content, message.model, message.usage
    - type: "system" — system messages (skipped for transcripts, but may have metadata)
    - type: "file-history-snapshot", "queue-operation" — internal (skipped)
    """
    try:
        data = json.loads(line.strip())
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(data, dict):
        return None

    msg_type = data.get("type", "")

    # Skip non-conversation entries
    if msg_type in ("file-history-snapshot", "queue-operation", "system"):
        # But extract metadata from system entries if useful
        return None

    entry = {
        "role": None,
        "content": None,
        "token_count": None,
        "timestamp": data.get("timestamp"),
        # Metadata for session enrichment
        "model": None,
        "git_branch": data.get("gitBranch"),
        "session_id": data.get("sessionId"),
        "cwd": data.get("cwd"),
        "usage": None,
        "slug": data.get("slug"),
        "effort_level": data.get("effortLevel"),
    }

    message = data.get("message", {})
    if not isinstance(message, dict):
        message = {}

    if msg_type == "user":
        entry["role"] = "user"
        # Skip meta/system user entries (commands, caveats, etc.)
        if data.get("isMeta"):
            return None
        # Get content from message.content (real user messages)
        content = message.get("content", "")
        # Fall back to top-level content only if no message content
        if not content:
            content = data.get("content", "")
        # Skip system XML tags
        if isinstance(content, str) and (
            content.startswith("<command-") or content.startswith("<local-command") or content.startswith("<system-")
        ):
            return None
        # Handle tool results embedded in user entries
        tool_result = data.get("toolUseResult")
        if tool_result:
            content = _format_tool_result(tool_result)
            entry["role"] = "tool_result"
        entry["content"] = _extract_content(content)

    elif msg_type == "assistant":
        entry["role"] = "assistant"
        content = message.get("content", "")
        entry["content"] = _extract_content(content)
        entry["model"] = message.get("model")

        # Extract token usage
        usage = message.get("usage", {})
        if usage:
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            cache_creation = usage.get("cache_creation_input_tokens", 0)
            cache_read = usage.get("cache_read_input_tokens", 0)
            entry["token_count"] = input_tokens + output_tokens
            entry["usage"] = {
                "input_tokens": input_tokens + cache_creation + cache_read,
                "output_tokens": output_tokens,
                "cache_tokens": cache_creation + cache_read,
            }

    elif msg_type == "tool_result":
        entry["role"] = "tool_result"
        entry["content"] = _extract_content(data.get("content", ""))

    elif msg_type == "result":
        entry["role"] = "assistant"
        entry["content"] = _extract_content(data.get("result", ""))

    elif "role" in message:
        entry["role"] = message["role"]
        entry["content"] = _extract_content(message.get("content", ""))

    if entry["role"] is None or entry["content"] is None:
        return None

    return entry


def _format_tool_result(result) -> str:
    """Format a tool result for clean display. No truncation — the UI handles overflow."""
    if isinstance(result, str):
        return result

    if isinstance(result, dict):
        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")
        if stdout or stderr:
            return stdout or stderr

        # File operation results (Write, Edit, Read)
        file_path = result.get("filePath") or result.get("file_path", "")
        op_type = result.get("type", "")
        content = result.get("content", "")
        if file_path:
            label = f"{op_type}: {file_path}" if op_type else file_path
            if content:
                return f"{label}\n{content}"
            return label

        return json.dumps(result, indent=2, ensure_ascii=False)

    return str(result)


def _format_tool_summary(name: str, inp: dict) -> str:
    """Format a tool call's input into a readable summary. No truncation."""
    if not isinstance(inp, dict):
        return str(inp)

    name_lower = name.lower()

    if name_lower in ("read",):
        return inp.get("file_path", "")

    if name_lower in ("write",):
        fp = inp.get("file_path", "")
        content = inp.get("content", "")
        return f"{fp}\n{content}" if content else fp

    if name_lower in ("edit",):
        fp = inp.get("file_path", "")
        old = inp.get("old_string", "")
        new = inp.get("new_string", "")
        return f"{fp}\n--- {old}\n+++ {new}"

    if name_lower in ("bash",):
        cmd = inp.get("command", "")
        desc = inp.get("description", "")
        return f"$ {cmd}" + (f"\n# {desc}" if desc else "")

    if name_lower in ("grep",):
        return f"pattern: {inp.get('pattern', '')}  path: {inp.get('path', '.')}"

    if name_lower in ("glob",):
        return f"pattern: {inp.get('pattern', '')}  path: {inp.get('path', '.')}"

    if name_lower in ("agent",):
        return inp.get("prompt", inp.get("description", ""))

    if name_lower in ("taskupdate", "taskcreate"):
        return inp.get("subject", inp.get("description", ""))

    if name_lower in ("enterplanmode",):
        return inp.get("description", inp.get("reason", "Entering plan mode"))

    if name_lower in ("exitplanmode",):
        return inp.get("description", inp.get("reason", "Exiting plan mode"))

    # Generic: show all fields
    summary_parts = []
    for k, v in inp.items():
        summary_parts.append(f"{k}: {v}")
    return "\n".join(summary_parts)


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
                    inp = item.get("input", {})
                    # Format tool calls cleanly based on tool type
                    summary = _format_tool_summary(name, inp)
                    parts.append(f"[Tool: {name}]\n{summary}")
                elif item.get("type") == "tool_result":
                    result_content = item.get("content", "")
                    text = result_content if isinstance(result_content, str) else str(result_content)
                    parts.append(f"[Result] {text}")
        return "\n".join(parts) if parts else None

    if isinstance(content, dict) and content.get("type") == "text":
        return content.get("text")

    return str(content) if content else None


def _infer_context_max(model: str | None) -> int:
    """Infer the max context window size from the model name."""
    if not model:
        return 200000
    m = model.lower()
    # Models with 1M context: opus 4.6 with [1m], or any model ID containing "1m"
    if "opus" in m:
        return 1000000  # Opus 4.6 defaults to 1M
    if "sonnet" in m:
        return 200000
    if "haiku" in m:
        return 200000
    return 200000


def _generate_auto_title(task_description: str, git_branch: str | None = None) -> str | None:
    """Generate a short auto-title from the task description and git branch.

    Prefers the git branch name (cleaned up) since it's usually more descriptive
    than a truncated user message. Falls back to first ~5 words of task description.
    """
    # Try to derive a title from git branch (e.g., "feature/PROJ-42-fix-auth" → "Fix Auth")
    if git_branch:
        # Strip common prefixes
        branch = git_branch
        for prefix in ("feature/", "feat/", "fix/", "bugfix/", "hotfix/", "chore/", "refactor/"):
            if branch.lower().startswith(prefix):
                branch = branch[len(prefix) :]
                break
        # Strip ticket IDs (e.g., "PROJ-42-" or "CIT-357-")
        branch = re.sub(r"^[A-Z]+-\d+-", "", branch)
        if branch:
            # Convert kebab-case to title case
            words = branch.replace("-", " ").replace("_", " ").split()
            if len(words) >= 2:
                return " ".join(w.capitalize() for w in words[:6])

    # Fall back to truncated task description
    if not task_description or len(task_description.strip()) < 6:
        return None
    words = task_description.strip().split()
    if len(words) <= 5:
        return task_description.strip()
    return " ".join(words[:5]) + "..."


_TOOL_VERBS = {
    "edit": "Editing",
    "write": "Writing",
    "read": "Reading",
    "bash": "Running",
    "grep": "Searching",
    "glob": "Searching",
    "agent": "Running agent",
}


def _extract_activity_preview(entries: list[dict]) -> str | None:
    """Derive a single-line activity preview from the most recent parsed entries.

    Priority: tool call > assistant text > None.
    """
    for entry in reversed(entries):
        content = entry.get("content", "") or ""

        # Check for tool calls: [Tool: Name]\nsummary
        if "[Tool: " in content:
            # Extract the last tool call in the content
            tool_matches = re.findall(r"\[Tool: (\w+)\]\n?(.*?)(?=\[Tool: |\Z)", content, re.DOTALL)
            if tool_matches:
                name, summary = tool_matches[-1]
                verb = _TOOL_VERBS.get(name.lower(), f"Using {name}")
                # Extract file path or command from summary
                first_line = summary.strip().split("\n")[0].strip() if summary.strip() else ""
                if first_line:
                    # Shorten file paths
                    if "/" in first_line:
                        parts = first_line.split("/")
                        first_line = "/".join(parts[-2:]) if len(parts) > 2 else first_line
                    # For Bash, strip the $ prefix
                    if name.lower() == "bash" and first_line.startswith("$ "):
                        first_line = first_line[2:]
                    return f"{verb}: {first_line}"[:100]
                return verb

        # Plain assistant text — take first sentence
        if entry.get("role") == "assistant" and content and "[Tool: " not in content:
            # First sentence or first 80 chars
            sentence = re.split(r"[.!?\n]", content)[0].strip()
            if sentence:
                return sentence[:80] + ("..." if len(sentence) > 80 else "")

    return None


def _session_id_from_path(file_path: str) -> str | None:
    """Extract a session identifier from a JSONL file path."""
    p = Path(file_path)
    if p.suffix != ".jsonl":
        return None
    return p.stem


def _parent_session_id_from_path(file_path: str) -> str | None:
    """Extract the parent session ID if this is a subagent transcript file.

    Subagent transcripts live at:
      .../<parent-session-id>/subagents/agent-<id>.jsonl
    """
    p = Path(file_path)
    if p.parent.name == "subagents":
        return p.parent.parent.name
    return None


async def _extract_ticket_id(branch_name: str) -> str | None:
    """Extract a Jira ticket ID from a git branch name using configured project keys."""
    raw = await db.get_setting("jira_project_keys")
    if not raw:
        return None
    try:
        keys = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not keys:
        return None
    pattern = r"(?:^|[/_-])(" + "|".join(re.escape(k) for k in keys) + r")-(\d+)"
    match = re.search(pattern, branch_name, re.IGNORECASE)
    if match:
        return f"{match.group(1).upper()}-{match.group(2)}"
    return None


async def _process_file_changes(file_path: str):
    """Read new lines from a JSONL file and store as transcripts.

    Also enriches session data with model, usage, and git branch info
    extracted from the JSONL entries.
    """
    session_id = _session_id_from_path(file_path)
    if not session_id:
        return

    last_pos = _file_positions.get(file_path, 0)

    try:
        with open(file_path, encoding="utf-8", errors="replace") as f:
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
    parent_id = _parent_session_id_from_path(file_path)
    if session is None:
        await db.create_session(session_id, project_path=str(Path(file_path).parent))
        if parent_id:
            await db.update_session(session_id, parent_session_id=parent_id, status="working")
    elif parent_id and not session.get("parent_session_id"):
        # Fix orphaned subagent: file path shows it's a subagent but DB is missing the link
        await db.update_session(session_id, parent_session_id=parent_id)

    # Track latest usage from new entries (not cumulative — use most recent snapshot)
    latest_input = 0
    latest_output = 0
    latest_cache = 0
    model = None
    git_branch = None
    cwd = None
    slug = None
    effort_level = None
    first_user_message = None
    parsed_entries = []

    for line in new_lines:
        line = line.strip()
        if not line:
            continue
        entry = _parse_jsonl_entry(line)
        if entry is None:
            continue

        # Collect metadata for session enrichment
        if entry.get("model"):
            model = entry["model"]
        if entry.get("git_branch"):
            git_branch = entry["git_branch"]
        if entry.get("cwd"):
            cwd = entry["cwd"]
        if entry.get("slug"):
            slug = entry["slug"]
        if entry.get("effort_level"):
            effort_level = entry["effort_level"]
        if entry["role"] == "user" and first_user_message is None and entry["content"]:
            # Skip command/meta/system messages as task descriptions
            c = entry["content"]
            if not c.startswith("<") and not c.startswith("{") and not c.startswith("[") and len(c) > 5:
                first_user_message = c[:200]
        # Use latest usage snapshot (each assistant message reports current context size)
        if entry.get("usage"):
            latest_input = entry["usage"]["input_tokens"]
            latest_output = entry["usage"]["output_tokens"]
            latest_cache = entry["usage"]["cache_tokens"]

        parsed_entries.append(entry)

        await db.add_transcript(
            session_id=session_id,
            role=entry["role"],
            content=entry["content"],
            source_file=file_path,
            token_count=entry.get("token_count"),
            timestamp=entry.get("timestamp"),
        )

    # Enrich session with discovered metadata
    # Always update last_activity_at when new entries arrive
    session_updates: dict = {"last_activity_at": datetime.now(UTC).isoformat()}
    # Infer status from entry content (fallback when hooks aren't reaching the server)
    session = await db.get_session(session_id)
    has_tool_activity = any(
        e.get("role") == "assistant" and e.get("content") and "[Tool:" in e["content"] for e in parsed_entries
    )
    if session:
        if session.get("status") == "stale":
            # Stale but new entries arriving — revive
            session_updates["status"] = "working" if has_tool_activity else "idle"
        elif has_tool_activity and session.get("status") in ("idle", None):
            # Tool calls detected — session is actively working
            session_updates["status"] = "working"
    if model:
        session_updates["model"] = model
    if slug:
        session_updates["session_name"] = slug
    if effort_level:
        session_updates["effort_level"] = effort_level
    if first_user_message:
        # Only set if not already set
        session = await db.get_session(session_id)
        if session and not session.get("task_description"):
            session_updates["task_description"] = first_user_message
    if git_branch:
        session_updates["git_branch"] = git_branch
    # Extract ticket from branch — use newly-seen branch or existing one from DB
    effective_branch = git_branch or (session.get("git_branch") if session else None)
    if effective_branch:
        session = session or await db.get_session(session_id)
        if not (session and session.get("ticket_id")):
            ticket_id = await _extract_ticket_id(effective_branch)
            if ticket_id:
                session_updates["ticket_id"] = ticket_id
    if cwd:
        session_updates["project_path"] = cwd
        project_name = cwd.rsplit("/", 1)[-1] if "/" in cwd else cwd
        session_updates["project_name"] = project_name
    # Look up PR/MR URL if we have branch + project path and no pr_url yet
    effective_cwd = cwd or (session_updates.get("project_path") if session_updates else None)
    effective_branch = git_branch or (session_updates.get("git_branch") if session_updates else None)
    if effective_cwd and effective_branch:
        session = session or await db.get_session(session_id)
        if not (session and session.get("pr_url")):
            try:
                pr_url = await find_pr_url(effective_cwd, effective_branch)
                if pr_url:
                    session_updates["pr_url"] = pr_url
            except Exception:
                pass  # Non-critical, skip silently

    if latest_input > 0 or latest_cache > 0:
        # The latest message's input+cache tokens = current context window size
        context_tokens = latest_input + latest_cache
        session_updates["input_tokens"] = latest_input
        session_updates["output_tokens"] = latest_output
        session_updates["cache_tokens"] = latest_cache
        session_updates["context_tokens"] = context_tokens
        # Infer max context from model name
        max_ctx = _infer_context_max(model)
        session_updates["context_max"] = max_ctx
        session_updates["context_usage_percent"] = min((context_tokens / max_ctx) * 100, 100)

    # Activity preview from latest entries
    preview = _extract_activity_preview(parsed_entries)
    if preview:
        session_updates["last_activity_preview"] = preview

    # Auto-generate display_name when missing and not locked
    if first_user_message:
        session = session or await db.get_session(session_id)
        if session and not session.get("display_name_locked") and not session.get("display_name"):
            auto_title = _generate_auto_title(first_user_message, git_branch)
            if auto_title:
                session_updates["display_name"] = auto_title

    if session_updates:
        await db.update_session(session_id, **session_updates)
        # Broadcast to dashboard clients
        try:
            from server.routes.ws import broadcast_session_update

            updated = await db.get_session(session_id)
            if updated:
                await broadcast_session_update(updated)
                # If this is a subagent, also keep the parent session alive
                pid = updated.get("parent_session_id")
                if pid:
                    parent = await db.get_session(pid)
                    if parent and parent.get("status") in ("stale", "idle"):
                        await db.update_session(
                            pid,
                            last_activity_at=datetime.now(UTC).isoformat(),
                            status="working",
                        )
                        parent = await db.get_session(pid)
                        if parent:
                            subagents = await db.get_subagents_for_session(pid)
                            parent["subagents"] = subagents
                            await broadcast_session_update(parent)
        except Exception:
            pass  # Non-critical


async def _debounced_process(file_path: str):
    """Debounce file processing to avoid excessive reads."""
    await asyncio.sleep(DEBOUNCE_SECONDS)
    await _process_file_changes(file_path)
    await _save_file_positions()


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

    await _load_file_positions()

    projects_dir = CLAUDE_PROJECTS_DIR
    if not os.path.isdir(projects_dir):
        logger.info("Claude projects directory not found: %s", projects_dir)
        return

    try:
        from watchfiles import Change, awatch

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
    for task in _debounce_tasks.values():
        if not task.done():
            task.cancel()
    _debounce_tasks.clear()
