"""FastAPI application entry point for Claude Code Command Center."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from server.db import init_db, close_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown."""
    await init_db()
    yield
    await close_db()


app = FastAPI(title="Claude Code Command Center", lifespan=lifespan)

# Mount static files
app.mount("/", StaticFiles(directory="public", html=True), name="static")
