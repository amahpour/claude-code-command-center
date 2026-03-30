"""FastAPI application entry point for Claude Code Command Center."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from server.db import close_db, init_db
from server.hooks import set_update_callback, start_stale_checker, stop_stale_checker
from server.routes.api import router as api_router
from server.routes.ws import broadcast_session_update
from server.routes.ws import router as ws_router
from server.watcher import start_watcher, stop_watcher


class NoCacheStaticMiddleware(BaseHTTPMiddleware):
    """Disable caching for static files during development."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path.endswith((".js", ".css", ".html")):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response


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
app.add_middleware(NoCacheStaticMiddleware)

# Include API and WebSocket routes
app.include_router(api_router)
app.include_router(ws_router)

# Mount static files (must be last — catches all unmatched routes)
static_dir = Path(__file__).parent.parent / "public"
app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
