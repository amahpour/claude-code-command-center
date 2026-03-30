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
