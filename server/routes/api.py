"""REST API routes for the Claude Code Command Center."""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from server import db
from server.hooks import process_hook_event

router = APIRouter(prefix="/api")


class HookEvent(BaseModel):
    """Incoming hook event from hook-handler.py."""
    event_type: str | None = None
    event: str | None = None
    session_id: str | None = None
    tool_name: str | None = None
    cwd: str | None = None
    project_path: str | None = None
    model: str | None = None
    message: str | None = None
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_tokens: int | None = None
    context_tokens: int | None = None
    context_max: int | None = None

    model_config = {"extra": "allow"}


class NewSessionRequest(BaseModel):
    project_dir: str
    prompt: str | None = None


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/sessions")
async def list_sessions():
    """List all active sessions, sorted by status priority."""
    sessions = await db.get_all_active_sessions()
    return {"sessions": sessions}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Get a single session with recent events."""
    session = await db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    events = await db.get_session_events(session_id, limit=50)
    return {"session": session, "events": events}


@router.get("/sessions/{session_id}/transcript")
async def get_transcript(
    session_id: str,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """Get the full transcript for a session."""
    session = await db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    transcripts = await db.get_session_transcripts(session_id, limit=limit, offset=offset)
    return {"session_id": session_id, "transcripts": transcripts}


@router.post("/hooks")
async def receive_hook(event: HookEvent):
    """Receive a hook event from hook-handler.py."""
    event_data = event.model_dump(exclude_none=True)
    # Include any extra fields
    if hasattr(event, "model_extra") and event.model_extra:
        event_data.update(event.model_extra)
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


@router.post("/sessions/new")
async def new_session(req: NewSessionRequest):
    """Launch a new Claude Code session."""
    from server.terminal import launch_session
    try:
        session_id = await launch_session(req.project_dir, req.prompt)
        return {"status": "ok", "session_id": session_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sessions/{session_id}/stop")
async def stop_session(session_id: str):
    """Stop a running session."""
    session = await db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    from server.terminal import stop_session as terminal_stop
    try:
        await terminal_stop(session_id)
        await db.update_session(session_id, status="completed")
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
