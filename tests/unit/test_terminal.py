"""Tests for terminal/PTY management."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from server.terminal import (
    _session_tmux_map,
    _tmux_session_name,
    launch_session,
    list_tmux_sessions,
    stop_session,
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


async def test_attach_session_not_in_map():
    """Test attach_session when session not in map and tmux doesn't have it."""
    from server.terminal import attach_session

    with patch("server.terminal._run_tmux") as mock_tmux:
        mock_tmux.return_value = MagicMock(returncode=1, stdout="", stderr="no session")
        result = await attach_session("nonexistent-sess")
        assert result is None


async def test_attach_session_tmux_not_found():
    """Test attach_session when tmux binary is not found."""
    from server.terminal import attach_session

    with patch("server.terminal._run_tmux", side_effect=FileNotFoundError):
        result = await attach_session("no-tmux-sess")
        assert result is None


async def test_detach_session():
    """Test detach_session closes fd and cleans up."""
    import os

    from server.terminal import _attached_fds, detach_session

    # Create a real fd pair so os.close works
    r, w = os.pipe()
    _attached_fds["detach-test"] = [r]

    await detach_session("detach-test", r)
    assert "detach-test" not in _attached_fds

    # Close w to avoid leak
    os.close(w)


async def test_detach_session_already_closed():
    """Test detach_session when fd is already closed."""
    from server.terminal import _attached_fds, detach_session

    _attached_fds["detach-closed"] = [9999]

    # Should not raise even if fd is invalid
    await detach_session("detach-closed", 9999)
    assert "detach-closed" not in _attached_fds


async def test_detach_session_fd_not_in_list():
    """Test detach_session when fd is not in the attached list."""
    import os

    from server.terminal import _attached_fds, detach_session

    r, w = os.pipe()
    _attached_fds["detach-missing"] = [r]

    await detach_session("detach-missing", 12345)  # fd not in list
    assert r in _attached_fds.get("detach-missing", [])

    # Cleanup
    os.close(r)
    os.close(w)
    _attached_fds.pop("detach-missing", None)


async def test_stop_session_with_tmux_name_in_map():
    """Test stop_session when session is tracked in the map."""
    import os

    from server.terminal import _attached_fds, _session_tmux_map
    from server.terminal import stop_session as term_stop

    _session_tmux_map["stop-test"] = "cccc-stop-test"
    r, w = os.pipe()
    _attached_fds["stop-test"] = [r]

    with patch("server.terminal._run_tmux") as mock_tmux:
        mock_tmux.return_value = MagicMock(returncode=0)
        await term_stop("stop-test")

    assert "stop-test" not in _session_tmux_map
    assert "stop-test" not in _attached_fds
    os.close(w)


async def test_stop_session_tmux_timeout():
    """Test stop_session when tmux times out."""
    from server.terminal import _session_tmux_map
    from server.terminal import stop_session as term_stop

    with patch("server.terminal._run_tmux", side_effect=subprocess.TimeoutExpired("tmux", 10)):
        await term_stop("timeout-sess")  # Should not raise

    assert "timeout-sess" not in _session_tmux_map


async def test_list_tmux_sessions_timeout():
    """Test listing sessions when tmux times out."""
    with patch("server.terminal._run_tmux", side_effect=subprocess.TimeoutExpired("tmux", 10)):
        sessions = await list_tmux_sessions()
        assert sessions == []


def test_run_tmux():
    """Test _run_tmux helper."""
    from server.terminal import _run_tmux

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        _run_tmux("list-sessions", check=False)
        mock_run.assert_called_once()
        assert "tmux" in mock_run.call_args[0][0]


async def test_attach_session_found_in_tmux():
    """Test attach when session is in tmux but not in map."""

    from server.terminal import _session_tmux_map, attach_session

    with patch("server.terminal._run_tmux") as mock_tmux:
        # has-session returns 0 (session exists)
        mock_tmux.return_value = MagicMock(returncode=0, stdout="", stderr="")
        with patch("os.openpty", return_value=(10, 11)):
            with patch("asyncio.create_subprocess_exec", new_callable=MagicMock) as mock_exec:
                mock_exec.return_value = MagicMock()
                # Make it an awaitable
                import asyncio as aio

                future = aio.Future()
                future.set_result(MagicMock())
                mock_exec.return_value = future
                with patch("os.close"):
                    result = await attach_session("attach-found")
                    assert result == 10

    _session_tmux_map.pop("attach-found", None)


async def test_attach_session_subprocess_fails():
    """Test attach when subprocess creation fails."""
    from server.terminal import _session_tmux_map, attach_session

    with patch("server.terminal._run_tmux") as mock_tmux:
        mock_tmux.return_value = MagicMock(returncode=0)
        with patch("os.openpty", return_value=(10, 11)):
            with patch("asyncio.create_subprocess_exec", side_effect=Exception("spawn failed")):
                with patch("os.close") as mock_close:
                    result = await attach_session("attach-fail")
                    assert result is None
                    # Both fds should be closed
                    assert mock_close.call_count >= 2

    _session_tmux_map.pop("attach-fail", None)


async def test_stop_session_closes_attached_fds():
    """Test that stop_session closes all attached FDs."""
    from server.terminal import _attached_fds, _session_tmux_map
    from server.terminal import stop_session as term_stop

    _session_tmux_map["stop-fds"] = "cccc-stop-fds"
    _attached_fds["stop-fds"] = [1001, 1002]

    with patch("server.terminal._run_tmux") as mock_tmux:
        mock_tmux.return_value = MagicMock(returncode=0)
        with patch("os.close") as mock_close:
            await term_stop("stop-fds")
            assert mock_close.call_count == 2

    assert "stop-fds" not in _attached_fds


async def test_stop_session_closes_fd_os_error():
    """Test stop_session handles OSError when closing fds."""
    from server.terminal import _attached_fds, _session_tmux_map
    from server.terminal import stop_session as term_stop

    _session_tmux_map["stop-oserr"] = "cccc-stop-oserr"
    _attached_fds["stop-oserr"] = [9998, 9999]

    with patch("server.terminal._run_tmux") as mock_tmux:
        mock_tmux.return_value = MagicMock(returncode=0)
        with patch("os.close", side_effect=OSError("bad fd")):
            await term_stop("stop-oserr")  # Should not raise

    assert "stop-oserr" not in _attached_fds
