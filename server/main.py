"""FastAPI application entry point for Claude Code Command Center."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from server.db import init_db, close_db
from server.hooks import start_stale_checker, stop_stale_checker, set_update_callback
from server.watcher import start_watcher, stop_watcher
from server.routes.api import router as api_router
from server.routes.ws import router as ws_router, broadcast_session_update

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown."""
    await init_db()
    set_update_callback(broadcast_session_update)
    start_stale_checker()
    await start_watcher()
    yield
    stop_watcher()
    stop_stale_checker()
    await close_db()


app = FastAPI(title="Claude Code Command Center", lifespan=lifespan)

# Include API and WebSocket routes
app.include_router(api_router)
app.include_router(ws_router)

# Mount static files (must be last — catches all unmatched routes)
static_dir = Path(__file__).parent.parent / "public"
app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
