"""Tests for WebSocket handlers and helpers."""

import json
import os
from unittest.mock import AsyncMock, patch

import pytest

from server.routes.ws import (
    _dashboard_clients,
    _read_pty_safe,
    _write_pty_safe,
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


# --- PTY helpers ---


def test_read_pty_safe_with_data():
    """_read_pty_safe returns data when available using real pipe."""
    r, w = os.pipe()
    os.write(w, b"hello")
    result = _read_pty_safe(r)
    assert result == b"hello"
    os.close(r)
    os.close(w)


def test_read_pty_safe_no_data():
    """_read_pty_safe returns None when no data available."""
    r, w = os.pipe()
    result = _read_pty_safe(r)
    assert result is None
    os.close(r)
    os.close(w)


def test_read_pty_safe_os_error():
    """_read_pty_safe returns None on bad fd."""
    result = _read_pty_safe(99999)
    assert result is None


def test_write_pty_safe_success():
    """_write_pty_safe writes data."""
    r, w = os.pipe()
    _write_pty_safe(w, b"hello")
    data = os.read(r, 100)
    assert data == b"hello"
    os.close(r)
    os.close(w)


def test_write_pty_safe_os_error():
    """_write_pty_safe silently handles bad fd."""
    _write_pty_safe(99999, b"hello")  # Should not raise


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


async def test_terminal_ws_no_session():
    """Test terminal WebSocket when no terminal session exists."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    import server.db as db
    from server.routes.ws import router

    await db.init_db(":memory:")

    app = FastAPI()
    app.include_router(router)

    with patch("server.terminal.attach_session", new_callable=AsyncMock, return_value=None):
        with TestClient(app) as client:
            with client.websocket_connect("/ws/terminal/nonexistent") as ws:
                data = ws.receive_json()
                assert data["type"] == "error"
                assert "No terminal found" in data["message"]

    await db.close_db()


async def test_terminal_ws_with_session():
    """Test terminal WebSocket with a mock PTY."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    import server.db as db
    from server.routes.ws import router

    await db.init_db(":memory:")

    app = FastAPI()
    app.include_router(router)

    r_fd, w_fd = os.pipe()

    with patch("server.terminal.attach_session", new_callable=AsyncMock, return_value=r_fd):
        with patch("server.terminal.detach_session", new_callable=AsyncMock):
            with TestClient(app) as client:
                with client.websocket_connect("/ws/terminal/test-sess") as ws:
                    # Send a ping
                    ws.send_text("ping")
                    pong = ws.receive_json()
                    assert pong["type"] == "pong"

    os.close(w_fd)
    try:
        os.close(r_fd)
    except OSError:
        pass
    await db.close_db()


async def test_terminal_ws_send_text():
    """Test terminal WebSocket receiving text data and writing to PTY."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    import server.db as db
    from server.routes.ws import router

    await db.init_db(":memory:")

    app = FastAPI()
    app.include_router(router)

    r_fd, w_fd = os.pipe()

    with patch("server.terminal.attach_session", new_callable=AsyncMock, return_value=w_fd):
        with patch("server.terminal.detach_session", new_callable=AsyncMock):
            with TestClient(app) as client:
                with client.websocket_connect("/ws/terminal/text-sess") as ws:
                    # Send text data
                    ws.send_text("hello world")
                    # Send ping
                    ws.send_text("ping")
                    pong = ws.receive_json()
                    assert pong["type"] == "pong"

    # Read what was written
    try:
        data = os.read(r_fd, 100)
        assert b"hello world" in data
    except OSError:
        pass
    for fd in (r_fd, w_fd):
        try:
            os.close(fd)
        except OSError:
            pass
    await db.close_db()


async def test_terminal_ws_send_bytes():
    """Test terminal WebSocket receiving binary data."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    import server.db as db
    from server.routes.ws import router

    await db.init_db(":memory:")

    app = FastAPI()
    app.include_router(router)

    r_fd, w_fd = os.pipe()

    with patch("server.terminal.attach_session", new_callable=AsyncMock, return_value=w_fd):
        with patch("server.terminal.detach_session", new_callable=AsyncMock):
            with TestClient(app) as client:
                with client.websocket_connect("/ws/terminal/bytes-sess") as ws:
                    ws.send_bytes(b"\x1b[A")  # Up arrow escape sequence

    for fd in (r_fd, w_fd):
        try:
            os.close(fd)
        except OSError:
            pass
    await db.close_db()


async def test_terminal_ws_exception_handling():
    """Test terminal WebSocket error path."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    import server.db as db
    from server.routes.ws import router

    await db.init_db(":memory:")

    app = FastAPI()
    app.include_router(router)

    with patch("server.terminal.attach_session", new_callable=AsyncMock, side_effect=Exception("attach failed")):
        with TestClient(app) as client:
            with client.websocket_connect("/ws/terminal/err-sess") as ws:
                data = ws.receive_json()
                assert data["type"] == "error"

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
