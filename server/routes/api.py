"""REST API routes for the Claude Code Command Center."""

import json

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from server import db
from server.hooks import process_hook_event

router = APIRouter(prefix="/api")


class HookEvent(BaseModel):
    """Incoming hook event from hook-handler.py."""

    model_config = {"extra": "allow", "populate_by_name": True}

    event_type: str | None = None
    event: str | None = None
    session_id: str | None = None
    tool_name: str | None = None
    cwd: str | None = None
    project_path: str | None = None
    session_model: str | None = None
    message: str | None = None
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_tokens: int | None = None
    context_tokens: int | None = None
    context_max: int | None = None


class NewSessionRequest(BaseModel):
    project_dir: str
    prompt: str | None = None


class SettingsUpdate(BaseModel):
    jira_project_keys: list[str] | None = None
    jira_server_url: str | None = None


_UNSET = object()


class SessionPatch(BaseModel):
    model_config = {"extra": "forbid"}
    ticket_id: str | None = _UNSET  # type: ignore[assignment]
    display_name: str | None = _UNSET  # type: ignore[assignment]
    display_name_locked: bool | None = _UNSET  # type: ignore[assignment]


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/sessions")
async def list_sessions():
    """List all active sessions with nested subagents."""
    sessions = await db.get_all_active_sessions()
    parent_ids = [s["id"] for s in sessions]
    subagents_map = await db.get_subagents_by_parent(parent_ids)
    for s in sessions:
        s["subagents"] = subagents_map.get(s["id"], [])
    return {"sessions": sessions}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Get a single session with recent events and subagents."""
    session = await db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    events = await db.get_session_events(session_id, limit=50)
    session["subagents"] = await db.get_subagents_for_session(session_id)
    return {"session": session, "events": events}


@router.get("/sessions/{session_id}/transcript")
async def get_transcript(
    session_id: str,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    after_id: int | None = Query(None, ge=0),
):
    """Get the transcript for a session.

    Use after_id for incremental updates (returns only entries with id > after_id).
    """
    session = await db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    transcripts = await db.get_session_transcripts(session_id, limit=limit, offset=offset, after_id=after_id)
    return {"session_id": session_id, "transcripts": transcripts}


@router.post("/hooks")
async def receive_hook(request: Request):
    """Receive a hook event from hook-handler.py."""
    event_data = await request.json()
    result = await process_hook_event(event_data)
    if result is None:
        return {"status": "ignored"}
    return {"status": "ok", "session": result}


@router.get("/history")
async def list_history(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List past sessions with pagination."""
    sessions = await db.get_all_sessions(limit=limit, offset=offset)
    return {"sessions": sessions, "limit": limit, "offset": offset}


@router.get("/search")
async def search(q: str = Query(..., min_length=1)):
    """Full-text search across transcripts."""
    results = await db.search_transcripts(q)
    return {"query": q, "results": results, "count": len(results)}


@router.get("/analytics/summary")
async def analytics_summary():
    """Token usage, costs, session counts."""
    summary = await db.get_analytics_summary()
    return summary


@router.get("/analytics/daily")
async def analytics_daily(days: int = Query(30, ge=1, le=365)):
    """Daily breakdown of usage."""
    daily = await db.get_analytics_daily(days=days)
    return {"days": daily}


@router.get("/settings")
async def get_settings():
    """Get all application settings."""
    raw = await db.get_all_settings()
    settings = {}
    for key, value in raw.items():
        try:
            settings[key] = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            settings[key] = value
    return {"settings": settings}


@router.put("/settings")
async def update_settings(req: SettingsUpdate):
    """Update application settings."""
    if req.jira_project_keys is not None:
        await db.set_setting("jira_project_keys", json.dumps(req.jira_project_keys))
    if req.jira_server_url is not None:
        await db.set_setting("jira_server_url", json.dumps(req.jira_server_url))
    return await get_settings()


@router.patch("/sessions/{session_id}")
async def patch_session(session_id: str, req: SessionPatch):
    """Update editable session fields (e.g., ticket_id)."""
    session = await db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    updates = {}
    if req.ticket_id is not _UNSET:
        updates["ticket_id"] = req.ticket_id or None
    if req.display_name is not _UNSET:
        updates["display_name"] = req.display_name or None
    if req.display_name_locked is not _UNSET:
        updates["display_name_locked"] = 1 if req.display_name_locked else 0  # type: ignore[assignment]
    if not updates:
        return {"session": session}
    updated = await db.update_session(session_id, **updates)
    from server.routes.ws import broadcast_session_update

    if updated:
        await broadcast_session_update(updated)
    return {"session": updated}


@router.get("/browse")
async def browse_directory(path: str = Query("~")):
    """List directories for the folder picker."""
    import os

    resolved = os.path.expanduser(path)
    if not os.path.isdir(resolved):
        raise HTTPException(status_code=400, detail="Not a directory")
    try:
        entries = []
        for name in sorted(os.listdir(resolved)):
            full = os.path.join(resolved, name)
            if name.startswith("."):
                continue  # Skip hidden files/dirs
            if os.path.isdir(full):
                entries.append({"name": name, "path": full, "type": "dir"})
        return {"path": resolved, "entries": entries}
    except PermissionError as e:
        raise HTTPException(status_code=403, detail="Permission denied") from e


@router.post("/sessions/new")
async def new_session(req: NewSessionRequest):
    """Launch a new Claude Code session (disabled — see GitHub issue)."""
    raise HTTPException(
        status_code=501,
        detail="New Session launching is temporarily disabled. Start Claude Code from your terminal instead — it will appear on the dashboard automatically via hooks.",
    )
