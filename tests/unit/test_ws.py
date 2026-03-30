"""Tests for WebSocket handlers."""

import json
from unittest.mock import AsyncMock

import pytest

from server.routes.ws import (
    _dashboard_clients,
    broadcast_session_update,
)


@pytest.fixture(autouse=True)
def clean_clients():
    _dashboard_clients.clear()
    yield
    _dashboard_clients.clear()


# --- broadcast_session_update ---


async def test_broadcast_no_clients():
    """No-op when no clients connected."""
    await broadcast_session_update({"id": "s1", "status": "idle"})


async def test_broadcast_sends_to_clients():
    ws1 = AsyncMock()
    ws2 = AsyncMock()
    _dashboard_clients.add(ws1)
    _dashboard_clients.add(ws2)

    session = {"id": "s1", "status": "working"}
    await broadcast_session_update(session)

    ws1.send_text.assert_called_once()
    ws2.send_text.assert_called_once()

    msg = json.loads(ws1.send_text.call_args[0][0])
    assert msg["type"] == "session_update"
    assert msg["session"]["id"] == "s1"


async def test_broadcast_removes_disconnected():
    ws_good = AsyncMock()
    ws_bad = AsyncMock()
    ws_bad.send_text.side_effect = Exception("disconnected")

    _dashboard_clients.add(ws_good)
    _dashboard_clients.add(ws_bad)

    await broadcast_session_update({"id": "s1"})

    assert ws_bad not in _dashboard_clients
    assert ws_good in _dashboard_clients


# --- WebSocket endpoints (via test client) ---


async def test_dashboard_ws_lifecycle():
    """Test dashboard WebSocket connect, initial state, ping/pong, disconnect."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    import server.db as db

    await db.init_db(":memory:")

    app = FastAPI()
    from server.routes.ws import router

    app.include_router(router)

    with TestClient(app) as client, client.websocket_connect("/ws/dashboard") as ws:
        # Should receive initial_state
        data = ws.receive_json()
        assert data["type"] == "initial_state"
        assert "sessions" in data

        # Send ping, receive pong
        ws.send_text("ping")
        pong = ws.receive_json()
        assert pong["type"] == "pong"

    await db.close_db()


async def test_dashboard_ws_exception_path():
    """Test dashboard WebSocket general exception handling."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    import server.db as db
    from server.routes.ws import _dashboard_clients, router

    await db.init_db(":memory:")

    app = FastAPI()
    app.include_router(router)

    with TestClient(app) as client, client.websocket_connect("/ws/dashboard") as ws:
        data = ws.receive_json()
        assert data["type"] == "initial_state"

    # Client disconnected, should be removed
    assert len(_dashboard_clients) == 0
    await db.close_db()
