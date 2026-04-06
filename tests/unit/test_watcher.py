"""Tests for the JSONL file watcher."""

import asyncio
import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import server.db as db
from server.watcher import (
    _extract_activity_preview,
    _extract_content,
    _file_positions,
    _generate_auto_title,
    _parse_jsonl_entry,
    _process_file_changes,
    _session_id_from_path,
)


@pytest.fixture(autouse=True)
async def setup_db():
    await db.init_db(":memory:")
    _file_positions.clear()
    yield
    await db.close_db()


def test_parse_user_message():
    line = json.dumps(
        {
            "type": "user",
            "message": {"role": "user", "content": "Fix the auth bug"},
            "timestamp": "2026-03-29T10:00:00Z",
        }
    )
    entry = _parse_jsonl_entry(line)
    assert entry is not None
    assert entry["role"] == "user"
    assert entry["content"] == "Fix the auth bug"
    assert entry["timestamp"] == "2026-03-29T10:00:00Z"


def test_parse_assistant_text():
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "I'll look into the auth module..."}],
            },
            "timestamp": "2026-03-29T10:00:05Z",
        }
    )
    entry = _parse_jsonl_entry(line)
    assert entry is not None
    assert entry["role"] == "assistant"
    assert "auth module" in entry["content"]


def test_parse_assistant_with_tool_use():
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Let me read the file."},
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "src/auth.ts"}},
                ],
            },
        }
    )
    entry = _parse_jsonl_entry(line)
    assert entry is not None
    assert entry["role"] == "assistant"
    assert "[Tool: Read]" in entry["content"]
    assert "auth.ts" in entry["content"]


def test_parse_tool_result():
    line = json.dumps(
        {
            "type": "tool_result",
            "tool_use_id": "toolu_123",
            "content": "file contents here...",
            "timestamp": "2026-03-29T10:00:06Z",
        }
    )
    entry = _parse_jsonl_entry(line)
    assert entry is not None
    assert entry["role"] == "tool_result"
    assert entry["content"] == "file contents here..."


def test_parse_invalid_json():
    entry = _parse_jsonl_entry("not valid json")
    assert entry is None


def test_parse_empty_content():
    line = json.dumps({"type": "user", "message": {"role": "user", "content": ""}})
    entry = _parse_jsonl_entry(line)
    assert entry is None


def test_parse_with_usage():
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": "Hello",
                "model": "claude-opus-4-6",
                "usage": {"input_tokens": 100, "output_tokens": 50},
            },
        }
    )
    entry = _parse_jsonl_entry(line)
    assert entry is not None
    assert entry["token_count"] == 150
    assert entry["model"] == "claude-opus-4-6"
    assert entry["usage"]["input_tokens"] == 100
    assert entry["usage"]["output_tokens"] == 50


def test_extract_content_string():
    assert _extract_content("hello") == "hello"


def test_extract_content_list():
    content = [
        {"type": "text", "text": "Part 1"},
        {"type": "text", "text": "Part 2"},
    ]
    result = _extract_content(content)
    assert "Part 1" in result
    assert "Part 2" in result


def test_extract_content_empty():
    assert _extract_content("") is None
    assert _extract_content(None) is None


def test_session_id_from_path():
    assert _session_id_from_path("/home/user/.claude/projects/abc/sess123.jsonl") == "sess123"
    assert _session_id_from_path("/tmp/test.txt") is None


async def test_process_file_changes():
    """Test reading new lines from a JSONL file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, dir=tempfile.gettempdir()) as f:
        f.write(
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "Hello world"},
                    "timestamp": "2026-03-29T10:00:00Z",
                }
            )
            + "\n"
        )
        f.write(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"role": "assistant", "content": "Hi there!"},
                    "timestamp": "2026-03-29T10:00:01Z",
                }
            )
            + "\n"
        )
        tmp_path = f.name

    try:
        await _process_file_changes(tmp_path)
        session_id = os.path.splitext(os.path.basename(tmp_path))[0]
        transcripts = await db.get_session_transcripts(session_id)
        assert len(transcripts) == 2
        assert transcripts[0]["role"] == "user"
        assert transcripts[0]["content"] == "Hello world"
        assert transcripts[1]["role"] == "assistant"
    finally:
        os.unlink(tmp_path)


async def test_incremental_reading():
    """Test that only new lines are read on subsequent calls."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, dir=tempfile.gettempdir()) as f:
        f.write(
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "First message"},
                }
            )
            + "\n"
        )
        tmp_path = f.name

    try:
        await _process_file_changes(tmp_path)
        session_id = os.path.splitext(os.path.basename(tmp_path))[0]

        # Add more lines
        with open(tmp_path, "a") as f:
            f.write(
                json.dumps(
                    {
                        "type": "user",
                        "message": {"role": "user", "content": "Second message"},
                    }
                )
                + "\n"
            )

        await _process_file_changes(tmp_path)
        transcripts = await db.get_session_transcripts(session_id)
        assert len(transcripts) == 2
        assert transcripts[0]["content"] == "First message"
        assert transcripts[1]["content"] == "Second message"
    finally:
        os.unlink(tmp_path)


def test_parse_result_type():
    line = json.dumps(
        {
            "type": "result",
            "result": "Task completed successfully",
        }
    )
    entry = _parse_jsonl_entry(line)
    assert entry is not None
    assert entry["role"] == "assistant"
    assert entry["content"] == "Task completed successfully"


def test_large_tool_input_not_truncated():
    """Tool inputs are no longer truncated — the UI handles overflow."""
    large_content = "x" * 1000
    large_input = {"file_path": "/tmp/big.py", "content": large_content}
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "name": "Write", "input": large_input}],
            },
        }
    )
    entry = _parse_jsonl_entry(line)
    assert entry is not None
    assert "/tmp/big.py" in entry["content"]
    assert "x" * 100 in entry["content"]  # Full content preserved, not truncated


# --- _format_tool_result ---


def test_format_tool_result_string():
    from server.watcher import _format_tool_result

    assert _format_tool_result("hello") == "hello"


def test_format_tool_result_stdout():
    from server.watcher import _format_tool_result

    result = _format_tool_result({"stdout": "output here", "stderr": ""})
    assert result == "output here"


def test_format_tool_result_stderr():
    from server.watcher import _format_tool_result

    result = _format_tool_result({"stdout": "", "stderr": "error here"})
    assert result == "error here"


def test_format_tool_result_file_path():
    from server.watcher import _format_tool_result

    result = _format_tool_result({"filePath": "/tmp/test.py", "type": "Write", "content": "code"})
    assert "/tmp/test.py" in result
    assert "Write" in result
    assert "code" in result


def test_format_tool_result_file_path_no_content():
    from server.watcher import _format_tool_result

    result = _format_tool_result({"file_path": "/tmp/test.py"})
    assert result == "/tmp/test.py"


def test_format_tool_result_dict_fallback():
    from server.watcher import _format_tool_result

    result = _format_tool_result({"key": "value"})
    assert "key" in result  # JSON serialized


def test_format_tool_result_other_type():
    from server.watcher import _format_tool_result

    assert _format_tool_result(42) == "42"


# --- _format_tool_summary ---


def test_format_tool_summary_read():
    from server.watcher import _format_tool_summary

    assert _format_tool_summary("Read", {"file_path": "/tmp/a.py"}) == "/tmp/a.py"


def test_format_tool_summary_write():
    from server.watcher import _format_tool_summary

    result = _format_tool_summary("Write", {"file_path": "/tmp/b.py", "content": "hello"})
    assert "/tmp/b.py" in result
    assert "hello" in result


def test_format_tool_summary_write_no_content():
    from server.watcher import _format_tool_summary

    result = _format_tool_summary("Write", {"file_path": "/tmp/b.py"})
    assert result == "/tmp/b.py"


def test_format_tool_summary_edit():
    from server.watcher import _format_tool_summary

    result = _format_tool_summary("Edit", {"file_path": "/tmp/c.py", "old_string": "old", "new_string": "new"})
    assert "---" in result
    assert "+++" in result


def test_format_tool_summary_bash():
    from server.watcher import _format_tool_summary

    result = _format_tool_summary("Bash", {"command": "ls -la", "description": "list files"})
    assert "$ ls -la" in result
    assert "# list files" in result


def test_format_tool_summary_bash_no_desc():
    from server.watcher import _format_tool_summary

    result = _format_tool_summary("Bash", {"command": "pwd"})
    assert result == "$ pwd"


def test_format_tool_summary_grep():
    from server.watcher import _format_tool_summary

    result = _format_tool_summary("Grep", {"pattern": "TODO", "path": "src/"})
    assert "TODO" in result
    assert "src/" in result


def test_format_tool_summary_glob():
    from server.watcher import _format_tool_summary

    result = _format_tool_summary("Glob", {"pattern": "*.py", "path": "."})
    assert "*.py" in result


def test_format_tool_summary_agent():
    from server.watcher import _format_tool_summary

    result = _format_tool_summary("Agent", {"prompt": "do something"})
    assert result == "do something"


def test_format_tool_summary_agent_with_description():
    from server.watcher import _format_tool_summary

    result = _format_tool_summary("Agent", {"description": "agent desc"})
    assert result == "agent desc"


def test_format_tool_summary_taskupdate():
    from server.watcher import _format_tool_summary

    result = _format_tool_summary("TaskUpdate", {"subject": "task subject"})
    assert result == "task subject"


def test_format_tool_summary_enter_plan_mode():
    from server.watcher import _format_tool_summary

    result = _format_tool_summary("EnterPlanMode", {"description": "Planning implementation"})
    assert result == "Planning implementation"


def test_format_tool_summary_enter_plan_mode_default():
    from server.watcher import _format_tool_summary

    result = _format_tool_summary("EnterPlanMode", {})
    assert result == "Entering plan mode"


def test_format_tool_summary_exit_plan_mode():
    from server.watcher import _format_tool_summary

    result = _format_tool_summary("ExitPlanMode", {"description": "Plan complete"})
    assert result == "Plan complete"


def test_format_tool_summary_exit_plan_mode_default():
    from server.watcher import _format_tool_summary

    result = _format_tool_summary("ExitPlanMode", {})
    assert result == "Exiting plan mode"


def test_format_tool_summary_generic():
    from server.watcher import _format_tool_summary

    result = _format_tool_summary("CustomTool", {"arg1": "val1", "arg2": "val2"})
    assert "arg1: val1" in result
    assert "arg2: val2" in result


def test_format_tool_summary_non_dict():
    from server.watcher import _format_tool_summary

    assert _format_tool_summary("Tool", "just a string") == "just a string"


# --- _extract_content edge cases ---


def test_extract_content_dict_text():
    result = _extract_content({"type": "text", "text": "hello"})
    assert result == "hello"


def test_extract_content_dict_other():
    result = _extract_content({"type": "image", "url": "http://example.com"})
    assert result is not None  # str() fallback


def test_extract_content_list_with_strings():
    result = _extract_content(["hello", "world"])
    assert "hello" in result
    assert "world" in result


def test_extract_content_list_with_tool_result():
    content = [{"type": "tool_result", "content": "tool output here"}]
    result = _extract_content(content)
    assert "[Result]" in result
    assert "tool output here" in result


def test_extract_content_list_with_tool_result_non_string():
    content = [{"type": "tool_result", "content": {"key": "val"}}]
    result = _extract_content(content)
    assert "[Result]" in result


def test_extract_content_none():
    assert _extract_content(None) is None


def test_extract_content_zero():
    """Non-empty non-None content gets str() fallback."""
    result = _extract_content(42)
    assert result == "42"


def test_extract_content_empty_list():
    assert _extract_content([]) is None


def test_extract_content_whitespace_string():
    assert _extract_content("   ") is None


# --- _infer_context_max ---


def test_infer_context_max():
    from server.watcher import _infer_context_max

    assert _infer_context_max(None) == 200000
    assert _infer_context_max("claude-opus-4-6") == 1000000
    assert _infer_context_max("claude-sonnet-4-6") == 200000
    assert _infer_context_max("claude-haiku-4-5") == 200000
    assert _infer_context_max("unknown-model") == 200000


# --- _extract_ticket_id ---


async def test_extract_ticket_id_match():
    from server.watcher import _extract_ticket_id

    await db.set_setting("jira_project_keys", '["PROJ", "DEV"]')
    result = await _extract_ticket_id("feature/PROJ-123-fix-bug")
    assert result == "PROJ-123"


async def test_extract_ticket_id_no_keys():
    from server.watcher import _extract_ticket_id

    result = await _extract_ticket_id("feature/PROJ-123")
    assert result is None


async def test_extract_ticket_id_no_match():
    from server.watcher import _extract_ticket_id

    await db.set_setting("jira_project_keys", '["PROJ"]')
    result = await _extract_ticket_id("feature/no-ticket-here")
    assert result is None


async def test_extract_ticket_id_bad_json():
    from server.watcher import _extract_ticket_id

    await db.set_setting("jira_project_keys", "not-json")
    result = await _extract_ticket_id("feature/PROJ-1")
    assert result is None


async def test_extract_ticket_id_empty_keys():
    from server.watcher import _extract_ticket_id

    await db.set_setting("jira_project_keys", "[]")
    result = await _extract_ticket_id("feature/PROJ-1")
    assert result is None


# --- _parse_jsonl_entry edge cases ---


def test_parse_skipped_types():
    for t in ("file-history-snapshot", "queue-operation", "system"):
        line = json.dumps({"type": t})
        assert _parse_jsonl_entry(line) is None


def test_parse_non_dict():
    assert _parse_jsonl_entry('"just a string"') is None


def test_parse_user_meta_message():
    line = json.dumps(
        {
            "type": "user",
            "isMeta": True,
            "message": {"role": "user", "content": "meta info"},
        }
    )
    assert _parse_jsonl_entry(line) is None


def test_parse_user_system_xml():
    line = json.dumps(
        {
            "type": "user",
            "message": {"role": "user", "content": "<command-start>test</command-start>"},
        }
    )
    assert _parse_jsonl_entry(line) is None


def test_parse_user_with_tool_result():
    line = json.dumps(
        {
            "type": "user",
            "message": {"role": "user", "content": ""},
            "toolUseResult": {"stdout": "output here", "stderr": ""},
        }
    )
    entry = _parse_jsonl_entry(line)
    assert entry is not None
    assert entry["role"] == "tool_result"
    assert entry["content"] == "output here"


def test_parse_assistant_with_cache_tokens():
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": "Response text",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_creation_input_tokens": 20,
                    "cache_read_input_tokens": 30,
                },
            },
        }
    )
    entry = _parse_jsonl_entry(line)
    assert entry is not None
    assert entry["usage"]["cache_tokens"] == 50  # 20 + 30
    assert entry["usage"]["input_tokens"] == 150  # 100 + 20 + 30


def test_parse_unknown_type_with_role_in_message():
    line = json.dumps(
        {
            "type": "unknown",
            "message": {"role": "system", "content": "some system message"},
        }
    )
    entry = _parse_jsonl_entry(line)
    assert entry is not None
    assert entry["role"] == "system"


def test_parse_entry_with_metadata():
    line = json.dumps(
        {
            "type": "assistant",
            "message": {"role": "assistant", "content": "Hello"},
            "gitBranch": "main",
            "sessionId": "sess-1",
            "cwd": "/tmp",
            "slug": "my-session",
            "effortLevel": "high",
        }
    )
    entry = _parse_jsonl_entry(line)
    assert entry["git_branch"] == "main"
    assert entry["session_id"] == "sess-1"
    assert entry["cwd"] == "/tmp"
    assert entry["slug"] == "my-session"
    assert entry["effort_level"] == "high"


def test_parse_message_not_dict():
    """When message field is not a dict."""
    line = json.dumps(
        {
            "type": "user",
            "message": "just a string",
        }
    )
    entry = _parse_jsonl_entry(line)
    # content comes from message.get("content") which fails -> falls back
    # The entry should be None since no content can be extracted
    assert entry is None


def test_parse_user_fallback_to_top_level_content():
    """User message falls back to top-level content."""
    line = json.dumps(
        {
            "type": "user",
            "message": {"role": "user", "content": ""},
            "content": "top-level content",
        }
    )
    entry = _parse_jsonl_entry(line)
    assert entry is not None
    assert entry["content"] == "top-level content"


# --- Enrichment in _process_file_changes ---


async def test_process_file_with_metadata_enrichment():
    """Test that session is enriched with model, branch, cwd from JSONL entries."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, dir=tempfile.gettempdir()) as f:
        f.write(
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "Fix the bug in authentication module"},
                    "timestamp": "2026-03-29T10:00:00Z",
                    "gitBranch": "feature/PROJ-42-fix-auth",
                    "cwd": "/home/user/myproject",
                }
            )
            + "\n"
        )
        f.write(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": "Looking into it",
                        "model": "claude-opus-4-6",
                        "usage": {
                            "input_tokens": 500,
                            "output_tokens": 100,
                            "cache_creation_input_tokens": 0,
                            "cache_read_input_tokens": 0,
                        },
                    },
                    "slug": "fix-auth",
                    "effortLevel": "high",
                }
            )
            + "\n"
        )
        tmp_path = f.name

    try:
        await _process_file_changes(tmp_path)
        session_id = os.path.splitext(os.path.basename(tmp_path))[0]
        session = await db.get_session(session_id)
        assert session is not None
        assert session["model"] == "claude-opus-4-6"
        assert session["git_branch"] == "feature/PROJ-42-fix-auth"
        assert session["project_path"] == "/home/user/myproject"
        assert session["project_name"] == "myproject"
        assert session["session_name"] == "fix-auth"
        assert session["effort_level"] == "high"
        assert session["task_description"] == "Fix the bug in authentication module"
        assert session["context_tokens"] > 0
        assert session["context_usage_percent"] > 0
    finally:
        os.unlink(tmp_path)


async def test_process_file_with_ticket_extraction():
    """Test that ticket ID is extracted from branch name."""
    await db.set_setting("jira_project_keys", '["PROJ"]')

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, dir=tempfile.gettempdir()) as f:
        f.write(
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "Working on the ticket"},
                    "gitBranch": "feature/PROJ-42-fix-auth",
                }
            )
            + "\n"
        )
        tmp_path = f.name

    try:
        await _process_file_changes(tmp_path)
        session_id = os.path.splitext(os.path.basename(tmp_path))[0]
        session = await db.get_session(session_id)
        assert session["ticket_id"] == "PROJ-42"
    finally:
        os.unlink(tmp_path)


async def test_process_file_pr_lookup():
    """Test that PR URL is looked up and stored."""
    from unittest.mock import AsyncMock, patch

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, dir=tempfile.gettempdir()) as f:
        f.write(
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "Working on PR"},
                    "gitBranch": "feature/my-pr",
                    "cwd": "/home/user/repo",
                }
            )
            + "\n"
        )
        tmp_path = f.name

    try:
        with patch(
            "server.watcher.find_pr_url", new_callable=AsyncMock, return_value="https://github.com/org/repo/pull/99"
        ):
            await _process_file_changes(tmp_path)
            session_id = os.path.splitext(os.path.basename(tmp_path))[0]
            session = await db.get_session(session_id)
            assert session["pr_url"] == "https://github.com/org/repo/pull/99"
    finally:
        os.unlink(tmp_path)


async def test_process_file_pr_lookup_failure():
    """PR lookup failure should not crash processing."""
    from unittest.mock import AsyncMock, patch

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, dir=tempfile.gettempdir()) as f:
        f.write(
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "Working on something"},
                    "gitBranch": "main",
                    "cwd": "/tmp/proj",
                }
            )
            + "\n"
        )
        tmp_path = f.name

    try:
        with patch("server.watcher.find_pr_url", new_callable=AsyncMock, side_effect=Exception("lookup failed")):
            await _process_file_changes(tmp_path)  # Should not raise
    finally:
        os.unlink(tmp_path)


async def test_process_file_unreadable():
    """Test processing a file that can't be read."""
    _file_positions.clear()
    await _process_file_changes("/nonexistent/file.jsonl")
    # Should not raise


async def test_process_file_no_session_id():
    """Test processing a non-JSONL file path."""
    await _process_file_changes("/tmp/not-a-jsonl.txt")
    # Should return early, no error


# --- _generate_auto_title ---


def test_generate_auto_title_from_branch():
    """Should derive title from cleaned-up git branch name."""
    assert _generate_auto_title("some task", "feature/PROJ-42-fix-auth-bug") == "Fix Auth Bug"
    assert _generate_auto_title("some task", "feat/add-dark-mode") == "Add Dark Mode"
    assert _generate_auto_title("some task", "fix/CIT-357-table-inline-filters") == "Table Inline Filters"


def test_generate_auto_title_branch_no_prefix():
    """Branch without common prefix still works."""
    assert _generate_auto_title("task", "improve-search-results") == "Improve Search Results"


def test_generate_auto_title_fallback_to_description():
    """Falls back to truncated description when branch is unhelpful."""
    assert _generate_auto_title("Fix the auth bug in login", None) == "Fix the auth bug in..."
    assert _generate_auto_title("Fix the auth bug in login", "main") is not None


def test_generate_auto_title_short_description():
    assert _generate_auto_title("Fix auth bug", None) == "Fix auth bug"


def test_generate_auto_title_empty():
    assert _generate_auto_title("", None) is None
    assert _generate_auto_title(None, None) is None


def test_generate_auto_title_tiny():
    assert _generate_auto_title("Hi", None) is None


# --- _extract_activity_preview ---


def test_extract_activity_preview_tool_call():
    """Entry with [Tool: Edit] returns 'Editing: ...'."""
    entries = [{"role": "assistant", "content": "[Tool: Edit]\n/home/user/project/server/watcher.py\n--- old\n+++ new"}]
    result = _extract_activity_preview(entries)
    assert result is not None
    assert "Editing" in result
    assert "watcher.py" in result


def test_extract_activity_preview_bash():
    """Entry with [Tool: Bash] returns 'Running: ...'."""
    entries = [{"role": "assistant", "content": "[Tool: Bash]\n$ pytest tests/"}]
    result = _extract_activity_preview(entries)
    assert result is not None
    assert "Running" in result
    assert "pytest" in result


def test_extract_activity_preview_read():
    entries = [{"role": "assistant", "content": "[Tool: Read]\n/tmp/file.py"}]
    result = _extract_activity_preview(entries)
    assert result is not None
    assert "Reading" in result


def test_extract_activity_preview_text_only():
    """Assistant text returns first sentence."""
    entries = [{"role": "assistant", "content": "I'll look into the authentication module. Let me read the file."}]
    result = _extract_activity_preview(entries)
    assert result is not None
    assert "authentication module" in result


def test_extract_activity_preview_empty():
    assert _extract_activity_preview([]) is None


def test_extract_activity_preview_uses_latest():
    """Should use the most recent entry."""
    entries = [
        {"role": "assistant", "content": "Old message"},
        {"role": "assistant", "content": "[Tool: Edit]\n/tmp/new.py\n--- old\n+++ new"},
    ]
    result = _extract_activity_preview(entries)
    assert "Editing" in result


# --- Auto-title + preview in _process_file_changes ---


async def test_process_file_auto_title_from_branch():
    """Auto-title should use git branch when available."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, dir=tempfile.gettempdir()) as f:
        f.write(
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "Fix the authentication bug in the login module"},
                    "timestamp": "2026-03-29T10:00:00Z",
                    "gitBranch": "feature/PROJ-42-fix-auth-bug",
                }
            )
            + "\n"
        )
        tmp_path = f.name

    try:
        with patch("server.routes.ws.broadcast_session_update", new_callable=AsyncMock):
            await _process_file_changes(tmp_path)
        session_id = os.path.splitext(os.path.basename(tmp_path))[0]
        session = await db.get_session(session_id)
        assert session["display_name"] == "Fix Auth Bug"
    finally:
        os.unlink(tmp_path)


async def test_process_file_locked_skips_auto_title():
    """Locked sessions should not have their display_name overwritten."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, dir=tempfile.gettempdir()) as f:
        f.write(
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "Fix the authentication bug"},
                    "gitBranch": "feature/something-else",
                }
            )
            + "\n"
        )
        tmp_path = f.name

    try:
        session_id = os.path.splitext(os.path.basename(tmp_path))[0]
        await db.create_session(session_id)
        await db.update_session(session_id, display_name="My custom title", display_name_locked=1)

        with patch("server.routes.ws.broadcast_session_update", new_callable=AsyncMock):
            await _process_file_changes(tmp_path)
        session = await db.get_session(session_id)
        assert session["display_name"] == "My custom title"
    finally:
        os.unlink(tmp_path)


async def test_process_file_preview_updated():
    """Preview should be set from tool call entries."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, dir=tempfile.gettempdir()) as f:
        f.write(
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "Fix the bug"},
                }
            )
            + "\n"
        )
        f.write(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "Let me edit the file."},
                            {
                                "type": "tool_use",
                                "name": "Edit",
                                "input": {
                                    "file_path": "/tmp/server/watcher.py",
                                    "old_string": "old",
                                    "new_string": "new",
                                },
                            },
                        ],
                    },
                }
            )
            + "\n"
        )
        tmp_path = f.name

    try:
        with patch("server.routes.ws.broadcast_session_update", new_callable=AsyncMock):
            await _process_file_changes(tmp_path)
        session_id = os.path.splitext(os.path.basename(tmp_path))[0]
        session = await db.get_session(session_id)
        assert session["last_activity_preview"] is not None
        assert "Editing" in session["last_activity_preview"]
    finally:
        os.unlink(tmp_path)


# --- _schedule_process / stop_watcher ---


async def test_schedule_process():
    from server.watcher import _debounce_tasks, _schedule_process

    with patch("server.watcher._debounced_process", new_callable=AsyncMock):
        _schedule_process("/tmp/test.jsonl")
        assert "/tmp/test.jsonl" in _debounce_tasks
        # Cancel to clean up
        task = _debounce_tasks["/tmp/test.jsonl"]
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def test_schedule_process_replaces_existing():
    from server.watcher import _debounce_tasks, _schedule_process

    with patch("server.watcher._debounced_process", new_callable=AsyncMock):
        _schedule_process("/tmp/replace.jsonl")
        first_task = _debounce_tasks["/tmp/replace.jsonl"]

        _schedule_process("/tmp/replace.jsonl")
        second_task = _debounce_tasks["/tmp/replace.jsonl"]

        assert first_task.cancelled() or first_task != second_task
        second_task.cancel()
        try:
            await second_task
        except asyncio.CancelledError:
            pass


def test_stop_watcher():
    from unittest.mock import MagicMock

    import server.watcher as watcher_mod
    from server.watcher import _debounce_tasks, stop_watcher

    mock_task = MagicMock()
    mock_task.done.return_value = False
    watcher_mod._watcher_task = mock_task

    mock_debounce = MagicMock()
    mock_debounce.done.return_value = False
    _debounce_tasks["test"] = mock_debounce

    stop_watcher()

    mock_task.cancel.assert_called_once()
    mock_debounce.cancel.assert_called_once()
    assert len(_debounce_tasks) == 0
    assert watcher_mod._watcher_task is None


async def test_start_watcher_no_dir():
    """start_watcher should return early if projects dir doesn't exist."""
    from server.watcher import start_watcher

    with patch("os.path.isdir", return_value=False):
        await start_watcher()
        # Should not have created a watcher task


async def test_process_file_skips_short_task_descriptions():
    """Short user messages (<= 5 chars) should not become task descriptions."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, dir=tempfile.gettempdir()) as f:
        f.write(
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "Hi"},
                }
            )
            + "\n"
        )
        tmp_path = f.name

    try:
        await _process_file_changes(tmp_path)
        session_id = os.path.splitext(os.path.basename(tmp_path))[0]
        session = await db.get_session(session_id)
        assert session.get("task_description") is None
    finally:
        os.unlink(tmp_path)


async def test_process_file_empty_lines():
    """Test processing file with only empty lines after existing content."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, dir=tempfile.gettempdir()) as f:
        f.write(
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "Hello there from test"},
                }
            )
            + "\n"
        )
        tmp_path = f.name

    try:
        await _process_file_changes(tmp_path)

        # Add only empty/blank lines
        with open(tmp_path, "a") as f:
            f.write("\n\n\n")

        await _process_file_changes(tmp_path)
        session_id = os.path.splitext(os.path.basename(tmp_path))[0]
        transcripts = await db.get_session_transcripts(session_id)
        assert len(transcripts) == 1  # Only the original message
    finally:
        os.unlink(tmp_path)


async def test_process_file_task_description_not_overwritten():
    """Task description should not be overwritten if already set."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, dir=tempfile.gettempdir()) as f:
        tmp_path = f.name
        session_id = os.path.splitext(os.path.basename(tmp_path))[0]

    # Pre-create session with task_description
    await db.create_session(session_id, task_description="Existing task")

    with open(tmp_path, "w") as f:
        f.write(
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "This is a new user message that is long enough"},
                }
            )
            + "\n"
        )

    try:
        await _process_file_changes(tmp_path)
        session = await db.get_session(session_id)
        assert session["task_description"] == "Existing task"
    finally:
        os.unlink(tmp_path)


async def test_start_watcher_with_dir():
    """start_watcher should create a task when projects dir exists."""
    import server.watcher as watcher_mod

    with patch("os.path.isdir", return_value=True), patch("asyncio.create_task") as mock_task:
        mock_task.return_value = MagicMock()
        # Mock watchfiles import
        await watcher_mod.start_watcher()

    # cleanup
    if watcher_mod._watcher_task and not isinstance(watcher_mod._watcher_task, MagicMock):
        watcher_mod._watcher_task.cancel()
    watcher_mod._watcher_task = None


async def test_process_file_with_context_tokens():
    """Test that context usage is calculated from latest usage snapshot."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, dir=tempfile.gettempdir()) as f:
        f.write(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": "Response",
                        "model": "claude-sonnet-4-6",
                        "usage": {
                            "input_tokens": 50000,
                            "output_tokens": 1000,
                            "cache_creation_input_tokens": 10000,
                            "cache_read_input_tokens": 5000,
                        },
                    },
                }
            )
            + "\n"
        )
        tmp_path = f.name

    try:
        await _process_file_changes(tmp_path)
        session_id = os.path.splitext(os.path.basename(tmp_path))[0]
        session = await db.get_session(session_id)
        # context_tokens = input + cache = 65000 + 15000 = 80000... let me recalculate
        # input_tokens = 50000 + 10000 + 5000 = 65000
        # cache_tokens = 10000 + 5000 = 15000
        # context_tokens = input_tokens + cache_tokens = 65000 + 15000 = 80000
        assert session["input_tokens"] == 65000
        assert session["cache_tokens"] == 15000
        assert session["context_tokens"] == 80000  # 65000 + 15000
        assert session["context_max"] == 200000  # sonnet
        assert session["context_usage_percent"] == 40.0  # 80000/200000*100
    finally:
        os.unlink(tmp_path)


async def test_process_file_reads_empty_after_position():
    """File with no new content after last read should return early."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, dir=tempfile.gettempdir()) as f:
        f.write(
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "Initial content here"},
                }
            )
            + "\n"
        )
        tmp_path = f.name

    try:
        await _process_file_changes(tmp_path)
        # Read again with no new content
        await _process_file_changes(tmp_path)
        session_id = os.path.splitext(os.path.basename(tmp_path))[0]
        transcripts = await db.get_session_transcripts(session_id)
        assert len(transcripts) == 1
    finally:
        os.unlink(tmp_path)


async def test_process_file_with_unparseable_lines():
    """Unparseable lines should be skipped without error."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, dir=tempfile.gettempdir()) as f:
        f.write("not valid json\n")
        f.write(json.dumps({"type": "system", "data": "skip"}) + "\n")
        f.write(
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "Valid message here"},
                }
            )
            + "\n"
        )
        tmp_path = f.name

    try:
        await _process_file_changes(tmp_path)
        session_id = os.path.splitext(os.path.basename(tmp_path))[0]
        transcripts = await db.get_session_transcripts(session_id)
        assert len(transcripts) == 1
        assert transcripts[0]["content"] == "Valid message here"
    finally:
        os.unlink(tmp_path)


async def test_debounced_process():
    """Test _debounced_process sleeps then processes."""
    from server.watcher import _debounced_process

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with patch("server.watcher._process_file_changes", new_callable=AsyncMock) as mock_proc:
            await _debounced_process("/tmp/test.jsonl")
            mock_sleep.assert_called_once_with(1.0)
            mock_proc.assert_called_once_with("/tmp/test.jsonl")


async def test_start_watcher_import_error():
    """start_watcher handles missing watchfiles gracefully."""
    import builtins

    import server.watcher as watcher_mod

    original_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "watchfiles":
            raise ImportError("no watchfiles")
        return original_import(name, *args, **kwargs)

    with patch("os.path.isdir", return_value=True), patch("builtins.__import__", side_effect=mock_import):
        await watcher_mod.start_watcher()


async def test_process_file_skips_system_looking_content():
    """Messages starting with < or { or [ should not become task descriptions."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, dir=tempfile.gettempdir()) as f:
        f.write(
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "{json object here}"},
                }
            )
            + "\n"
        )
        f.write(
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "This is a real task for the session"},
                }
            )
            + "\n"
        )
        tmp_path = f.name

    try:
        await _process_file_changes(tmp_path)
        session_id = os.path.splitext(os.path.basename(tmp_path))[0]
        session = await db.get_session(session_id)
        assert session["task_description"] == "This is a real task for the session"
    finally:
        os.unlink(tmp_path)


async def test_process_file_fixes_orphaned_subagent():
    """Watcher should set parent_session_id on existing sessions that lack it."""
    # Create a subagent session without parent_session_id (simulates orphaned state)
    await db.create_session("agent-orphan-1")
    session = await db.get_session("agent-orphan-1")
    assert session["parent_session_id"] is None

    # Create a JSONL file at a subagent path (parent/subagents/agent-orphan-1.jsonl)
    parent_dir = tempfile.mkdtemp()
    subagent_dir = os.path.join(parent_dir, "parent-sess-1", "subagents")
    os.makedirs(subagent_dir)
    jsonl_path = os.path.join(subagent_dir, "agent-orphan-1.jsonl")
    with open(jsonl_path, "w") as f:
        f.write(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"role": "assistant", "content": "Working on it"},
                    "timestamp": "2026-03-30T12:00:00Z",
                }
            )
            + "\n"
        )

    try:
        with patch("server.routes.ws.broadcast_session_update", new_callable=AsyncMock):
            await _process_file_changes(jsonl_path)

        # Verify parent_session_id was fixed
        session = await db.get_session("agent-orphan-1")
        assert session["parent_session_id"] == "parent-sess-1"
    finally:
        import shutil

        shutil.rmtree(parent_dir)


# --- AI Summary: _build_summary_prompt ---


def test_build_summary_prompt_basic():
    from server.watcher import _build_summary_prompt

    session = {
        "display_name": "Auth Refactor",
        "project_name": "myproject",
        "git_branch": "feat/auth",
        "ticket_id": "PROJ-42",
        "pr_url": "https://github.com/org/repo/pull/7",
    }
    conversation = [
        {"role": "user", "content": "Fix the auth middleware"},
        {"role": "assistant", "content": "I will update the JWT handling"},
    ]
    prompt = _build_summary_prompt(session, conversation)
    assert "Auth Refactor" in prompt
    assert "feat/auth" in prompt
    assert "PROJ-42" in prompt
    assert "https://github.com/org/repo/pull/7" in prompt
    assert "[user] Fix the auth middleware" in prompt
    assert "[assistant] I will update the JWT handling" in prompt
    assert '"title"' in prompt
    assert '"ticket_id"' in prompt
    assert '"pr_url"' in prompt


def test_build_summary_prompt_missing_fields():
    from server.watcher import _build_summary_prompt

    session = {
        "display_name": None,
        "project_name": None,
        "git_branch": None,
        "ticket_id": None,
        "pr_url": None,
    }
    prompt = _build_summary_prompt(session, [])
    assert "Untitled" in prompt
    assert "unknown" in prompt
    assert "Ticket ID: none" in prompt
    assert "PR URL: none" in prompt


def test_build_summary_prompt_truncates_long_content():
    from server.watcher import _build_summary_prompt

    session = {"display_name": "Test", "git_branch": "main"}
    long_msg = "x" * 500
    conversation = [{"role": "user", "content": long_msg}]
    prompt = _build_summary_prompt(session, conversation)
    # Content should be truncated to 200 chars
    assert "x" * 200 in prompt
    assert "x" * 201 not in prompt


# --- AI Summary: _parse_summary_response ---


def test_parse_summary_response_valid():
    from server.watcher import _parse_summary_response

    raw = '{"title": "Fix Auth Bug", "ticket_id": "PROJ-42", "pr_url": "https://github.com/o/r/pull/1"}'
    result = _parse_summary_response(raw)
    assert result is not None
    assert result["title"] == "Fix Auth Bug"
    assert result["ticket_id"] == "PROJ-42"
    assert result["pr_url"] == "https://github.com/o/r/pull/1"


def test_parse_summary_response_nulls():
    from server.watcher import _parse_summary_response

    raw = '{"title": "Working on tests", "ticket_id": null, "pr_url": null}'
    result = _parse_summary_response(raw)
    assert result is not None
    assert result["title"] == "Working on tests"
    assert result["ticket_id"] is None
    assert result["pr_url"] is None


def test_parse_summary_response_strips_markdown_fences():
    from server.watcher import _parse_summary_response

    raw = '```json\n{"title": "Auth Refactor", "ticket_id": null, "pr_url": null}\n```'
    result = _parse_summary_response(raw)
    assert result is not None
    assert result["title"] == "Auth Refactor"


def test_parse_summary_response_strips_plain_fences():
    from server.watcher import _parse_summary_response

    raw = '```\n{"title": "Debug Login", "ticket_id": null, "pr_url": null}\n```'
    result = _parse_summary_response(raw)
    assert result is not None
    assert result["title"] == "Debug Login"


def test_parse_summary_response_invalid_json():
    from server.watcher import _parse_summary_response

    assert _parse_summary_response("not json at all") is None


def test_parse_summary_response_missing_title():
    from server.watcher import _parse_summary_response

    assert _parse_summary_response('{"ticket_id": "X-1"}') is None


def test_parse_summary_response_empty_title():
    from server.watcher import _parse_summary_response

    assert _parse_summary_response('{"title": "", "ticket_id": null, "pr_url": null}') is None


def test_parse_summary_response_title_too_long():
    from server.watcher import _parse_summary_response

    long_title = "x" * 101
    raw = json.dumps({"title": long_title, "ticket_id": None, "pr_url": None})
    assert _parse_summary_response(raw) is None


def test_parse_summary_response_non_dict():
    from server.watcher import _parse_summary_response

    assert _parse_summary_response("[1, 2, 3]") is None


def test_parse_summary_response_non_string_ticket():
    from server.watcher import _parse_summary_response

    raw = '{"title": "Test", "ticket_id": 42, "pr_url": true}'
    result = _parse_summary_response(raw)
    assert result is not None
    assert result["title"] == "Test"
    assert result["ticket_id"] is None
    assert result["pr_url"] is None


def test_parse_summary_response_whitespace_title():
    from server.watcher import _parse_summary_response

    raw = '{"title": "  Fix Auth  ", "ticket_id": null, "pr_url": null}'
    result = _parse_summary_response(raw)
    assert result is not None
    assert result["title"] == "Fix Auth"


# --- AI Summary: _get_summary_interval ---


async def test_get_summary_interval_default():
    from server.watcher import SUMMARY_TRIGGER_INTERVAL, _get_summary_interval

    result = await _get_summary_interval()
    assert result == SUMMARY_TRIGGER_INTERVAL


async def test_get_summary_interval_from_settings():
    from server.watcher import _get_summary_interval

    await db.set_setting("summary_interval", "10")
    result = await _get_summary_interval()
    assert result == 10


async def test_get_summary_interval_invalid_json():
    from server.watcher import SUMMARY_TRIGGER_INTERVAL, _get_summary_interval

    await db.set_setting("summary_interval", "not-a-number")
    result = await _get_summary_interval()
    assert result == SUMMARY_TRIGGER_INTERVAL


async def test_get_summary_interval_zero():
    from server.watcher import SUMMARY_TRIGGER_INTERVAL, _get_summary_interval

    await db.set_setting("summary_interval", "0")
    result = await _get_summary_interval()
    assert result == SUMMARY_TRIGGER_INTERVAL


async def test_get_summary_interval_negative():
    from server.watcher import SUMMARY_TRIGGER_INTERVAL, _get_summary_interval

    await db.set_setting("summary_interval", "-5")
    result = await _get_summary_interval()
    assert result == SUMMARY_TRIGGER_INTERVAL


async def test_get_summary_interval_float():
    from server.watcher import SUMMARY_TRIGGER_INTERVAL, _get_summary_interval

    await db.set_setting("summary_interval", "3.5")
    result = await _get_summary_interval()
    assert result == SUMMARY_TRIGGER_INTERVAL


# --- AI Summary: _should_generate_summary ---


async def test_should_generate_summary_happy_path():
    import server.watcher as watcher_mod
    from server.watcher import _should_generate_summary

    watcher_mod._user_message_counts["s1"] = 5
    watcher_mod._claude_available = True
    session = {"status": "working", "display_name_locked": False, "parent_session_id": None}
    assert await _should_generate_summary("s1", session) is True
    watcher_mod._user_message_counts.pop("s1", None)


async def test_should_generate_summary_not_on_threshold():
    import server.watcher as watcher_mod
    from server.watcher import _should_generate_summary

    watcher_mod._user_message_counts["s2"] = 3
    watcher_mod._claude_available = True
    session = {"status": "working", "display_name_locked": False, "parent_session_id": None}
    assert await _should_generate_summary("s2", session) is False
    watcher_mod._user_message_counts.pop("s2", None)


async def test_should_generate_summary_locked():
    import server.watcher as watcher_mod
    from server.watcher import _should_generate_summary

    watcher_mod._user_message_counts["s3"] = 5
    watcher_mod._claude_available = True
    session = {"status": "working", "display_name_locked": True, "parent_session_id": None}
    assert await _should_generate_summary("s3", session) is False
    watcher_mod._user_message_counts.pop("s3", None)


async def test_should_generate_summary_completed():
    import server.watcher as watcher_mod
    from server.watcher import _should_generate_summary

    watcher_mod._user_message_counts["s4"] = 5
    watcher_mod._claude_available = True
    session = {"status": "completed", "display_name_locked": False, "parent_session_id": None}
    assert await _should_generate_summary("s4", session) is False
    watcher_mod._user_message_counts.pop("s4", None)


async def test_should_generate_summary_stale():
    import server.watcher as watcher_mod
    from server.watcher import _should_generate_summary

    watcher_mod._user_message_counts["s5"] = 5
    watcher_mod._claude_available = True
    session = {"status": "stale", "display_name_locked": False, "parent_session_id": None}
    assert await _should_generate_summary("s5", session) is False
    watcher_mod._user_message_counts.pop("s5", None)


async def test_should_generate_summary_subagent():
    import server.watcher as watcher_mod
    from server.watcher import _should_generate_summary

    watcher_mod._user_message_counts["s6"] = 5
    watcher_mod._claude_available = True
    session = {"status": "working", "display_name_locked": False, "parent_session_id": "parent-1"}
    assert await _should_generate_summary("s6", session) is False
    watcher_mod._user_message_counts.pop("s6", None)


async def test_should_generate_summary_claude_unavailable():
    import server.watcher as watcher_mod
    from server.watcher import _should_generate_summary

    watcher_mod._user_message_counts["s7"] = 5
    watcher_mod._claude_available = False
    session = {"status": "working", "display_name_locked": False, "parent_session_id": None}
    assert await _should_generate_summary("s7", session) is False
    watcher_mod._claude_available = True
    watcher_mod._user_message_counts.pop("s7", None)


async def test_should_generate_summary_no_session():
    from server.watcher import _should_generate_summary

    assert await _should_generate_summary("s8", None) is False


async def test_should_generate_summary_zero_count():
    import server.watcher as watcher_mod
    from server.watcher import _should_generate_summary

    watcher_mod._user_message_counts["s9"] = 0
    watcher_mod._claude_available = True
    session = {"status": "working", "display_name_locked": False, "parent_session_id": None}
    assert await _should_generate_summary("s9", session) is False
    watcher_mod._user_message_counts.pop("s9", None)


async def test_should_generate_summary_task_inflight():
    import server.watcher as watcher_mod
    from server.watcher import _should_generate_summary

    watcher_mod._user_message_counts["s10"] = 5
    watcher_mod._claude_available = True
    mock_task = MagicMock()
    mock_task.done.return_value = False
    watcher_mod._summary_tasks["s10"] = mock_task
    session = {"status": "working", "display_name_locked": False, "parent_session_id": None}
    assert await _should_generate_summary("s10", session) is False
    watcher_mod._summary_tasks.pop("s10", None)
    watcher_mod._user_message_counts.pop("s10", None)


# --- AI Summary: _generate_ai_summary ---


async def test_generate_ai_summary_success():
    from server.watcher import _generate_ai_summary

    await db.create_session("gen-1", project_path="/tmp/proj")
    await db.update_session("gen-1", git_branch="feat/auth", status="working")
    await db.add_transcript("gen-1", "user", "Fix the auth bug")
    await db.add_transcript("gen-1", "assistant", "I will fix the JWT validation")

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = (
        b'{"title": "Fix JWT Auth", "ticket_id": "AUTH-99", "pr_url": null}',
        b"",
    )

    with (
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc),
        patch("asyncio.wait_for", new_callable=AsyncMock, return_value=mock_proc.communicate.return_value),
        patch("server.routes.ws.broadcast_session_update", new_callable=AsyncMock),
    ):
        await _generate_ai_summary("gen-1")

    session = await db.get_session("gen-1")
    assert session["display_name"] == "Fix JWT Auth"
    assert session["ticket_id"] == "AUTH-99"


async def test_generate_ai_summary_does_not_overwrite_existing_ticket():
    from server.watcher import _generate_ai_summary

    await db.create_session("gen-2", project_path="/tmp/proj")
    await db.update_session("gen-2", ticket_id="EXISTING-1", status="working")
    await db.add_transcript("gen-2", "user", "Some work")

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = (
        b'{"title": "New Title", "ticket_id": "OTHER-2", "pr_url": "https://github.com/o/r/pull/5"}',
        b"",
    )

    with (
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc),
        patch("asyncio.wait_for", new_callable=AsyncMock, return_value=mock_proc.communicate.return_value),
        patch("server.routes.ws.broadcast_session_update", new_callable=AsyncMock),
    ):
        await _generate_ai_summary("gen-2")

    session = await db.get_session("gen-2")
    assert session["display_name"] == "New Title"
    assert session["ticket_id"] == "EXISTING-1"  # NOT overwritten
    assert session["pr_url"] == "https://github.com/o/r/pull/5"  # Filled in


async def test_generate_ai_summary_locked_session():
    from server.watcher import _generate_ai_summary

    await db.create_session("gen-3", project_path="/tmp/proj")
    await db.update_session("gen-3", display_name_locked=1, display_name="Locked Title")
    await db.add_transcript("gen-3", "user", "Some work")

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        await _generate_ai_summary("gen-3")
        mock_exec.assert_not_called()

    session = await db.get_session("gen-3")
    assert session["display_name"] == "Locked Title"


async def test_generate_ai_summary_no_conversation():
    from server.watcher import _generate_ai_summary

    await db.create_session("gen-4", project_path="/tmp/proj")
    # No transcripts added

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        await _generate_ai_summary("gen-4")
        mock_exec.assert_not_called()


async def test_generate_ai_summary_subprocess_failure():
    from server.watcher import _generate_ai_summary

    await db.create_session("gen-5", project_path="/tmp/proj")
    await db.update_session("gen-5", status="working")
    await db.add_transcript("gen-5", "user", "Do something")

    mock_proc = AsyncMock()
    mock_proc.returncode = 1
    mock_proc.communicate.return_value = (b"", b"Error: something went wrong")

    with (
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc),
        patch("asyncio.wait_for", new_callable=AsyncMock, return_value=mock_proc.communicate.return_value),
    ):
        await _generate_ai_summary("gen-5")

    session = await db.get_session("gen-5")
    assert session["display_name"] is None  # Not updated


async def test_generate_ai_summary_invalid_response():
    from server.watcher import _generate_ai_summary

    await db.create_session("gen-6", project_path="/tmp/proj")
    await db.update_session("gen-6", status="working")
    await db.add_transcript("gen-6", "user", "Do something")

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = (b"This is not JSON", b"")

    with (
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc),
        patch("asyncio.wait_for", new_callable=AsyncMock, return_value=mock_proc.communicate.return_value),
    ):
        await _generate_ai_summary("gen-6")

    session = await db.get_session("gen-6")
    assert session["display_name"] is None


async def test_generate_ai_summary_timeout():
    from server.watcher import _generate_ai_summary

    await db.create_session("gen-7", project_path="/tmp/proj")
    await db.update_session("gen-7", status="working")
    await db.add_transcript("gen-7", "user", "Do something")

    mock_proc = AsyncMock()

    with (
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc),
        patch("asyncio.wait_for", new_callable=AsyncMock, side_effect=TimeoutError),
    ):
        await _generate_ai_summary("gen-7")  # Should not raise

    session = await db.get_session("gen-7")
    assert session["display_name"] is None


async def test_generate_ai_summary_claude_not_found():
    import server.watcher as watcher_mod
    from server.watcher import _generate_ai_summary

    watcher_mod._claude_available = True
    await db.create_session("gen-8", project_path="/tmp/proj")
    await db.update_session("gen-8", status="working")
    await db.add_transcript("gen-8", "user", "Do something")

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, side_effect=FileNotFoundError):
        await _generate_ai_summary("gen-8")

    assert watcher_mod._claude_available is False
    watcher_mod._claude_available = True


async def test_generate_ai_summary_locked_during_subprocess():
    """If title gets locked while subprocess runs, update should be skipped."""
    from server.watcher import _generate_ai_summary

    await db.create_session("gen-9", project_path="/tmp/proj")
    await db.update_session("gen-9", status="working")
    await db.add_transcript("gen-9", "user", "Do something")

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = (
        b'{"title": "New Title", "ticket_id": null, "pr_url": null}',
        b"",
    )

    call_count = 0

    async def fake_wait_for(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        # Simulate user locking the title while subprocess was running
        await db.update_session("gen-9", display_name_locked=1, display_name="User Title")
        return mock_proc.communicate.return_value

    with (
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc),
        patch("asyncio.wait_for", side_effect=fake_wait_for),
        patch("server.routes.ws.broadcast_session_update", new_callable=AsyncMock),
    ):
        await _generate_ai_summary("gen-9")

    session = await db.get_session("gen-9")
    assert session["display_name"] == "User Title"  # Not overwritten


async def test_generate_ai_summary_cleans_up_task():
    """Summary task should be removed from _summary_tasks when complete."""
    import server.watcher as watcher_mod
    from server.watcher import _generate_ai_summary

    await db.create_session("gen-10", project_path="/tmp/proj")
    await db.update_session("gen-10", status="working")
    await db.add_transcript("gen-10", "user", "Do something")
    watcher_mod._summary_tasks["gen-10"] = MagicMock()

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = (
        b'{"title": "Done", "ticket_id": null, "pr_url": null}',
        b"",
    )

    with (
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc),
        patch("asyncio.wait_for", new_callable=AsyncMock, return_value=mock_proc.communicate.return_value),
        patch("server.routes.ws.broadcast_session_update", new_callable=AsyncMock),
    ):
        await _generate_ai_summary("gen-10")

    assert "gen-10" not in watcher_mod._summary_tasks


# --- AI Summary: counting in _process_file_changes ---


async def test_process_file_counts_user_messages_for_summary():
    """User message counting should trigger summary at threshold."""
    import server.watcher as watcher_mod

    watcher_mod._user_message_counts.clear()
    watcher_mod._claude_available = True

    lines = []
    for i in range(5):
        lines.append(
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": f"Message number {i + 1} from the user"},
                }
            )
        )
        lines.append(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"role": "assistant", "content": f"Reply {i + 1}"},
                }
            )
        )

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, dir=tempfile.gettempdir()) as f:
        f.write("\n".join(lines) + "\n")
        tmp_path = f.name

    session_id = os.path.splitext(os.path.basename(tmp_path))[0]

    try:
        with (
            patch("server.routes.ws.broadcast_session_update", new_callable=AsyncMock),
            patch("server.watcher._generate_ai_summary", new_callable=AsyncMock) as mock_gen,
        ):
            await _process_file_changes(tmp_path)

        assert watcher_mod._user_message_counts[session_id] == 5
        mock_gen.assert_called_once_with(session_id)
    finally:
        os.unlink(tmp_path)
        watcher_mod._user_message_counts.pop(session_id, None)


async def test_process_file_no_summary_below_threshold():
    """Summary should not trigger below the threshold."""
    import server.watcher as watcher_mod

    watcher_mod._user_message_counts.clear()
    watcher_mod._claude_available = True

    lines = []
    for i in range(3):
        lines.append(
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": f"Message {i + 1} here"},
                }
            )
        )

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, dir=tempfile.gettempdir()) as f:
        f.write("\n".join(lines) + "\n")
        tmp_path = f.name

    session_id = os.path.splitext(os.path.basename(tmp_path))[0]

    try:
        with (
            patch("server.routes.ws.broadcast_session_update", new_callable=AsyncMock),
            patch("server.watcher._generate_ai_summary", new_callable=AsyncMock) as mock_gen,
        ):
            await _process_file_changes(tmp_path)

        assert watcher_mod._user_message_counts[session_id] == 3
        mock_gen.assert_not_called()
    finally:
        os.unlink(tmp_path)
        watcher_mod._user_message_counts.pop(session_id, None)


# --- stop_watcher cleans up summary state ---


def test_stop_watcher_cleans_up_summary_tasks():
    import server.watcher as watcher_mod
    from server.watcher import stop_watcher

    watcher_mod._watcher_task = None

    mock_summary = MagicMock()
    mock_summary.done.return_value = False
    watcher_mod._summary_tasks["test-sess"] = mock_summary
    watcher_mod._user_message_counts["test-sess"] = 10

    stop_watcher()

    mock_summary.cancel.assert_called_once()
    assert len(watcher_mod._summary_tasks) == 0
    assert len(watcher_mod._user_message_counts) == 0
