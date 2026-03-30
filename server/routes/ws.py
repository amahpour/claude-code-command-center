"""WebSocket handlers for real-time dashboard updates and terminal streaming."""

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()
logger = logging.getLogger(__name__)

# Connected dashboard clients
_dashboard_clients: set[WebSocket] = set()

# Connected terminal clients: session_id -> set of websockets
_terminal_clients: dict[str, set[WebSocket]] = {}


async def broadcast_session_update(session: dict):
    """Broadcast a session state change to all connected dashboard clients."""
    if not _dashboard_clients:
        return

    message = json.dumps({"type": "session_update", "session": session})
    disconnected = set()
    for ws in _dashboard_clients:
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.add(ws)

    for ws in disconnected:
        _dashboard_clients.discard(ws)


@router.websocket("/ws/dashboard")
async def dashboard_ws(websocket: WebSocket):
    """WebSocket endpoint for dashboard real-time updates."""
    await websocket.accept()
    _dashboard_clients.add(websocket)
    logger.info("Dashboard client connected (%d total)", len(_dashboard_clients))

    try:
        # Send initial state
        from server.db import get_all_active_sessions, get_subagents_by_parent

        sessions = await get_all_active_sessions()
        parent_ids = [s["id"] for s in sessions]
        subagents_map = await get_subagents_by_parent(parent_ids)
        for s in sessions:
            s["subagents"] = subagents_map.get(s["id"], [])
        await websocket.send_text(
            json.dumps(
                {
                    "type": "initial_state",
                    "sessions": sessions,
                }
            )
        )

        # Keep connection alive, handle pings
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _dashboard_clients.discard(websocket)
        logger.info("Dashboard client disconnected (%d remaining)", len(_dashboard_clients))


@router.websocket("/ws/terminal/{session_id}")
async def terminal_ws(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for interactive terminal access."""
    await websocket.accept()

    if session_id not in _terminal_clients:
        _terminal_clients[session_id] = set()
    _terminal_clients[session_id].add(websocket)
    logger.info("Terminal client connected for session %s", session_id)

    try:
        from server.terminal import attach_session, detach_session

        pty_fd = await attach_session(session_id)
        if pty_fd is None:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "error",
                        "message": f"No terminal found for session {session_id}",
                    }
                )
            )
            await websocket.close()
            return

        # Read from PTY and send to WebSocket
        async def read_pty():
            loop = asyncio.get_event_loop()
            try:
                while True:
                    data = await loop.run_in_executor(None, lambda: _read_pty_safe(pty_fd))
                    if data:
                        await websocket.send_bytes(data)
                    else:
                        await asyncio.sleep(0.05)
            except (asyncio.CancelledError, Exception):
                pass

        read_task = asyncio.create_task(read_pty())

        try:
            # Read from WebSocket and write to PTY
            while True:
                data = await websocket.receive()
                if "text" in data:
                    text = data["text"]
                    if text == "ping":
                        await websocket.send_text(json.dumps({"type": "pong"}))
                        continue
                    _write_pty_safe(pty_fd, text.encode())
                elif "bytes" in data:
                    _write_pty_safe(pty_fd, data["bytes"])
        except WebSocketDisconnect:
            pass
        finally:
            read_task.cancel()
            await detach_session(session_id, pty_fd)

    except Exception as e:
        logger.exception("Terminal WebSocket error for session %s", session_id)
        try:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "error",
                        "message": str(e),
                    }
                )
            )
        except Exception:
            pass
    finally:
        if session_id in _terminal_clients:
            _terminal_clients[session_id].discard(websocket)
            if not _terminal_clients[session_id]:
                del _terminal_clients[session_id]
        logger.info("Terminal client disconnected for session %s", session_id)


def _read_pty_safe(fd) -> bytes | None:
    """Read from a PTY file descriptor safely."""
    import os
    import select

    try:
        r, _, _ = select.select([fd], [], [], 0.1)
        if r:
            return os.read(fd, 4096)
    except (OSError, ValueError):
        pass
    return None


def _write_pty_safe(fd, data: bytes):
    """Write to a PTY file descriptor safely."""
    import os

    try:
        os.write(fd, data)
    except (OSError, ValueError):
        pass
