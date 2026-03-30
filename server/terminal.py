"""Terminal/PTY management using tmux for session launching and interaction."""

import asyncio
import logging
import os
import subprocess
import uuid

logger = logging.getLogger(__name__)

# Map session_id -> tmux session name
_session_tmux_map: dict[str, str] = {}

# Map session_id -> list of attached PTY fds
_attached_fds: dict[str, list[int]] = {}


def _tmux_session_name(session_id: str) -> str:
    """Generate a tmux session name from a session ID."""
    short_id = session_id[:12] if len(session_id) > 12 else session_id
    return f"cccc-{short_id}"


def _run_tmux(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a tmux command."""
    cmd = ["tmux"] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=check)


async def launch_session(project_dir: str, initial_prompt: str | None = None) -> str:
    """Launch a new Claude Code session in a tmux session.

    Returns the session ID.
    """
    session_id = str(uuid.uuid4())[:8]
    tmux_name = _tmux_session_name(session_id)

    # Launch tmux with an interactive claude session
    claude_cmd = f"cd '{project_dir}' && claude"

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, lambda: _run_tmux("new-session", "-d", "-s", tmux_name, "-x", "200", "-y", "50", claude_cmd)
    )

    # Accept the trust dialog (sends Enter to confirm "Yes, I trust this folder")
    await asyncio.sleep(2)
    await loop.run_in_executor(None, lambda: _run_tmux("send-keys", "-t", tmux_name, "Enter", check=False))

    # Wait for Claude to start, then send the prompt if provided
    if initial_prompt:
        await asyncio.sleep(3)
        escaped = initial_prompt.replace("'", "'\\''")
        await loop.run_in_executor(None, lambda: _run_tmux("send-keys", "-t", tmux_name, escaped, "Enter", check=False))

    _session_tmux_map[session_id] = tmux_name
    logger.info("Launched tmux session %s for session %s in %s", tmux_name, session_id, project_dir)
    return session_id


async def attach_session(session_id: str) -> int | None:
    """Attach to a tmux session's PTY for WebSocket streaming.

    Returns a file descriptor for reading/writing, or None if not found.
    """
    tmux_name = _session_tmux_map.get(session_id)
    if not tmux_name:
        # Try to find it from tmux directly
        tmux_name = _tmux_session_name(session_id)
        try:
            result = _run_tmux("has-session", "-t", tmux_name, check=False)
            if result.returncode != 0:
                return None
            _session_tmux_map[session_id] = tmux_name
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None

    # Use a PTY pair to pipe tmux I/O
    master_fd, slave_fd = os.openpty()

    try:
        # Start a process that connects tmux to our PTY
        await asyncio.create_subprocess_exec(
            "tmux",
            "attach-session",
            "-t",
            tmux_name,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
        )
        os.close(slave_fd)

        if session_id not in _attached_fds:
            _attached_fds[session_id] = []
        _attached_fds[session_id].append(master_fd)

        return master_fd

    except Exception:
        logger.exception("Failed to attach to tmux session %s", tmux_name)
        os.close(master_fd)
        os.close(slave_fd)
        return None


async def detach_session(session_id: str, fd: int):
    """Detach from a terminal session, closing the PTY fd."""
    try:
        os.close(fd)
    except OSError:
        pass

    if session_id in _attached_fds:
        try:
            _attached_fds[session_id].remove(fd)
        except ValueError:
            pass
        if not _attached_fds[session_id]:
            del _attached_fds[session_id]


async def stop_session(session_id: str):
    """Stop a running tmux session."""
    tmux_name = _session_tmux_map.get(session_id)
    if not tmux_name:
        tmux_name = _tmux_session_name(session_id)

    loop = asyncio.get_event_loop()
    try:
        # Send Ctrl+C first
        await loop.run_in_executor(None, lambda: _run_tmux("send-keys", "-t", tmux_name, "C-c", check=False))
        await asyncio.sleep(1)
        # Kill the session
        await loop.run_in_executor(None, lambda: _run_tmux("kill-session", "-t", tmux_name, check=False))
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    _session_tmux_map.pop(session_id, None)

    # Close any attached FDs
    for fd in _attached_fds.pop(session_id, []):
        try:
            os.close(fd)
        except OSError:
            pass

    logger.info("Stopped tmux session %s for session %s", tmux_name, session_id)


async def list_tmux_sessions() -> list[dict]:
    """List all tmux sessions that belong to the command center."""
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: _run_tmux(
                "list-sessions", "-F", "#{session_name}:#{session_created}:#{session_attached}", check=False
            ),
        )
        if result.returncode != 0:
            return []

        sessions = []
        for line in result.stdout.strip().split("\n"):
            if not line or not line.startswith("cccc-"):
                continue
            parts = line.split(":", 2)
            sessions.append(
                {
                    "tmux_name": parts[0],
                    "created": parts[1] if len(parts) > 1 else None,
                    "attached": parts[2] if len(parts) > 2 else "0",
                }
            )
        return sessions

    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
