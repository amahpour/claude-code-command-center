"""WebSocket handlers for real-time dashboard updates."""

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()
logger = logging.getLogger(__name__)

# Connected dashboard clients
_dashboard_clients: set[WebSocket] = set()


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
