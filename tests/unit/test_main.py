"""Tests for the main FastAPI app entry point."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from starlette.testclient import TestClient


async def test_no_cache_middleware():
    """NoCacheStaticMiddleware adds no-cache headers to static files."""
    from server.main import NoCacheStaticMiddleware
    from fastapi import FastAPI, Request
    from starlette.responses import PlainTextResponse

    app = FastAPI()

    @app.get("/test.js")
    async def js_file():
        return PlainTextResponse("console.log('hi')")

    @app.get("/api/data")
    async def api():
        return PlainTextResponse("data")

    app.add_middleware(NoCacheStaticMiddleware)

    client = TestClient(app)

    # JS file should have no-cache headers
    resp = client.get("/test.js")
    assert resp.headers.get("Cache-Control") == "no-cache, no-store, must-revalidate"
    assert resp.headers.get("Pragma") == "no-cache"

    # API endpoint should NOT have no-cache headers
    resp = client.get("/api/data")
    assert "no-cache" not in resp.headers.get("Cache-Control", "")


async def test_lifespan():
    """Test that lifespan initializes and cleans up resources."""
    from server.main import lifespan
    from fastapi import FastAPI

    app = FastAPI()

    with patch("server.main.init_db", new_callable=AsyncMock) as mock_init, \
         patch("server.main.close_db", new_callable=AsyncMock) as mock_close, \
         patch("server.main.set_update_callback") as mock_set_cb, \
         patch("server.main.start_stale_checker") as mock_start_stale, \
         patch("server.main.stop_stale_checker") as mock_stop_stale, \
         patch("server.main.start_watcher", new_callable=AsyncMock) as mock_start_watcher, \
         patch("server.main.stop_watcher") as mock_stop_watcher:

        async with lifespan(app):
            mock_init.assert_called_once()
            mock_set_cb.assert_called_once()
            mock_start_stale.assert_called_once()
            mock_start_watcher.assert_called_once()

        mock_stop_watcher.assert_called_once()
        mock_stop_stale.assert_called_once()
        mock_close.assert_called_once()


def test_app_exists():
    """Test that the app is properly configured."""
    from server.main import app
    assert app.title == "Claude Code Command Center"
