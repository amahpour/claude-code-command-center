"""Tests for terminal/PTY management."""

import subprocess
import pytest
from unittest.mock import patch, MagicMock

from server.terminal import (
    _tmux_session_name,
    _session_tmux_map,
    launch_session,
    stop_session,
    list_tmux_sessions,
)


# Check if tmux is available
def _tmux_available():
    try:
        subprocess.run(["tmux", "-V"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


HAS_TMUX = _tmux_available()


def test_tmux_session_name():
    name = _tmux_session_name("abc123def456")
    assert name == "cccc-abc123def456"

    name = _tmux_session_name("abc123def456extra")
    assert name == "cccc-abc123def456"


@pytest.mark.skipif(not HAS_TMUX, reason="tmux not available")
async def test_launch_and_stop_session():
    """Test launching and stopping a tmux session."""
    # Use a simple command instead of claude
    with patch("server.terminal._run_tmux") as mock_tmux:
        mock_tmux.return_value = MagicMock(returncode=0, stdout="", stderr="")
        session_id = await launch_session("/tmp", initial_prompt=None)
        assert session_id is not None
        assert session_id in _session_tmux_map

        await stop_session(session_id)
        assert session_id not in _session_tmux_map


async def test_launch_session_with_prompt():
    """Test that launch_session sends prompt via send-keys after launch."""
    with patch("server.terminal._run_tmux") as mock_tmux:
        mock_tmux.return_value = MagicMock(returncode=0, stdout="", stderr="")
        session_id = await launch_session("/tmp/myproject", "Fix the auth bug")
        assert session_id is not None
        # Should be called 3 times: new-session, Enter (trust), send-keys (prompt)
        assert mock_tmux.call_count == 3
        # First call: new-session
        assert "new-session" in mock_tmux.call_args_list[0][0]
        # Third call: send the prompt
        assert "Fix the auth bug" in mock_tmux.call_args_list[2][0]


async def test_stop_nonexistent_session():
    """Stopping a session that doesn't exist should not raise."""
    await stop_session("nonexistent")


async def test_list_tmux_sessions_empty():
    """Test listing sessions when none exist."""
    with patch("server.terminal._run_tmux") as mock_tmux:
        mock_tmux.return_value = MagicMock(returncode=1, stdout="", stderr="no server running")
        sessions = await list_tmux_sessions()
        assert sessions == []


async def test_list_tmux_sessions_with_data():
    """Test listing sessions with cccc- prefix filtering."""
    with patch("server.terminal._run_tmux") as mock_tmux:
        mock_tmux.return_value = MagicMock(
            returncode=0,
            stdout="cccc-abc123:1234567890:0\nother-session:1234567890:1\ncccc-def456:1234567890:0\n",
            stderr="",
        )
        sessions = await list_tmux_sessions()
        assert len(sessions) == 2
        assert sessions[0]["tmux_name"] == "cccc-abc123"
        assert sessions[1]["tmux_name"] == "cccc-def456"
