"""Tests for the JSONL file watcher."""

import json
import os
import tempfile

import pytest
import server.db as db
from server.watcher import (
    _parse_jsonl_entry,
    _process_file_changes,
    _file_positions,
    _extract_content,
    _session_id_from_path,
)


@pytest.fixture(autouse=True)
async def setup_db():
    await db.init_db(":memory:")
    _file_positions.clear()
    yield
    await db.close_db()


def test_parse_user_message():
    line = json.dumps({
        "type": "user",
        "message": {"role": "user", "content": "Fix the auth bug"},
        "timestamp": "2026-03-29T10:00:00Z",
    })
    entry = _parse_jsonl_entry(line)
    assert entry is not None
    assert entry["role"] == "user"
    assert entry["content"] == "Fix the auth bug"
    assert entry["timestamp"] == "2026-03-29T10:00:00Z"


def test_parse_assistant_text():
    line = json.dumps({
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "I'll look into the auth module..."}],
        },
        "timestamp": "2026-03-29T10:00:05Z",
    })
    entry = _parse_jsonl_entry(line)
    assert entry is not None
    assert entry["role"] == "assistant"
    assert "auth module" in entry["content"]


def test_parse_assistant_with_tool_use():
    line = json.dumps({
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Let me read the file."},
                {"type": "tool_use", "name": "Read", "input": {"file_path": "src/auth.ts"}},
            ],
        },
    })
    entry = _parse_jsonl_entry(line)
    assert entry is not None
    assert entry["role"] == "assistant"
    assert "[Tool: Read]" in entry["content"]
    assert "auth.ts" in entry["content"]


def test_parse_tool_result():
    line = json.dumps({
        "type": "tool_result",
        "tool_use_id": "toolu_123",
        "content": "file contents here...",
        "timestamp": "2026-03-29T10:00:06Z",
    })
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
    line = json.dumps({
        "type": "assistant",
        "message": {"role": "assistant", "content": "Hello"},
        "usage": {"input_tokens": 100, "output_tokens": 50},
    })
    entry = _parse_jsonl_entry(line)
    assert entry is not None
    assert entry["token_count"] == 150


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
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, dir=tempfile.gettempdir()
    ) as f:
        f.write(json.dumps({
            "type": "user",
            "message": {"role": "user", "content": "Hello world"},
            "timestamp": "2026-03-29T10:00:00Z",
        }) + "\n")
        f.write(json.dumps({
            "type": "assistant",
            "message": {"role": "assistant", "content": "Hi there!"},
            "timestamp": "2026-03-29T10:00:01Z",
        }) + "\n")
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
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, dir=tempfile.gettempdir()
    ) as f:
        f.write(json.dumps({
            "type": "user",
            "message": {"role": "user", "content": "First message"},
        }) + "\n")
        tmp_path = f.name

    try:
        await _process_file_changes(tmp_path)
        session_id = os.path.splitext(os.path.basename(tmp_path))[0]

        # Add more lines
        with open(tmp_path, "a") as f:
            f.write(json.dumps({
                "type": "user",
                "message": {"role": "user", "content": "Second message"},
            }) + "\n")

        await _process_file_changes(tmp_path)
        transcripts = await db.get_session_transcripts(session_id)
        assert len(transcripts) == 2
        assert transcripts[0]["content"] == "First message"
        assert transcripts[1]["content"] == "Second message"
    finally:
        os.unlink(tmp_path)


def test_parse_result_type():
    line = json.dumps({
        "type": "result",
        "result": "Task completed successfully",
    })
    entry = _parse_jsonl_entry(line)
    assert entry is not None
    assert entry["role"] == "assistant"
    assert entry["content"] == "Task completed successfully"


def test_large_tool_input_truncated():
    large_input = {"data": "x" * 1000}
    line = json.dumps({
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "name": "Write", "input": large_input}],
        },
    })
    entry = _parse_jsonl_entry(line)
    assert entry is not None
    assert len(entry["content"]) < 600  # Truncated
    assert "..." in entry["content"]
