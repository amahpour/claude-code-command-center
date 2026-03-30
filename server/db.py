"""SQLite database setup and helpers using aiosqlite."""

import json
import os
from datetime import datetime, timezone

import aiosqlite

DB_DIR = os.path.expanduser("~/.claude-command-center")
DB_PATH = os.path.join(DB_DIR, "data.db")

_db: aiosqlite.Connection | None = None


def _get_db_path() -> str:
    """Return the database path, allowing override for tests."""
    return os.environ.get("CCCC_DB_PATH", DB_PATH)


async def get_db() -> aiosqlite.Connection:
    """Get the shared database connection."""
    global _db
    if _db is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _db


async def init_db(db_path: str | None = None):
    """Initialize the database, creating tables if they don't exist."""
    global _db
    path = db_path or _get_db_path()

    if path != ":memory:":
        os.makedirs(os.path.dirname(path), exist_ok=True)

    _db = await aiosqlite.connect(path)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA foreign_keys=ON")
    await _create_tables(_db)


async def close_db():
    """Close the database connection."""
    global _db
    if _db is not None:
        await _db.close()
        _db = None


async def _create_tables(db: aiosqlite.Connection):
    """Create all tables if they don't exist."""
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            project_path TEXT,
            project_name TEXT,
            session_name TEXT,
            git_branch TEXT,
            model TEXT,
            effort_level TEXT,
            status TEXT DEFAULT 'idle',
            task_description TEXT,
            context_usage_percent REAL DEFAULT 0,
            context_tokens INTEGER DEFAULT 0,
            context_max INTEGER DEFAULT 200000,
            cost_usd REAL DEFAULT 0,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cache_tokens INTEGER DEFAULT 0,
            pr_url TEXT,
            started_at TEXT,
            last_activity_at TEXT,
            ended_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            event_type TEXT,
            tool_name TEXT,
            payload TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        );

        CREATE TABLE IF NOT EXISTS transcripts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            source_file TEXT,
            role TEXT,
            content TEXT,
            token_count INTEGER,
            timestamp TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        );
    """)

    # Add columns that may not exist in older databases
    for col, col_type in [("session_name", "TEXT"), ("effort_level", "TEXT"), ("ticket_id", "TEXT"), ("display_name", "TEXT"), ("display_name_locked", "INTEGER DEFAULT 0"), ("last_activity_preview", "TEXT")]:
        try:
            await db.execute(f"ALTER TABLE sessions ADD COLUMN {col} {col_type}")
        except Exception:
            pass  # Column already exists

    # FTS5 virtual table — create separately since IF NOT EXISTS works differently
    try:
        await db.execute("""
            CREATE VIRTUAL TABLE transcripts_fts USING fts5(
                content,
                content='transcripts',
                content_rowid='id'
            );
        """)
    except Exception:
        pass  # Already exists

    await db.commit()


# --- Session CRUD ---

async def create_session(
    session_id: str,
    project_path: str | None = None,
    model: str | None = None,
    task_description: str | None = None,
) -> dict:
    """Create a new session record."""
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    project_name = os.path.basename(project_path) if project_path else None

    await db.execute(
        """INSERT INTO sessions (id, project_path, project_name, model,
           task_description, status, started_at, last_activity_at, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 'idle', ?, ?, ?, ?)""",
        (session_id, project_path, project_name, model,
         task_description, now, now, now, now),
    )
    await db.commit()
    return await get_session(session_id)


async def update_session(session_id: str, **kwargs) -> dict | None:
    """Update a session's fields. Pass any column name as a keyword argument."""
    db = await get_db()
    if not kwargs:
        return await get_session(session_id)

    kwargs["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [session_id]

    await db.execute(
        f"UPDATE sessions SET {set_clause} WHERE id = ?", values
    )
    await db.commit()
    return await get_session(session_id)


async def get_session(session_id: str) -> dict | None:
    """Get a single session by ID."""
    db = await get_db()
    cursor = await db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
    row = await cursor.fetchone()
    return dict(row) if row else None


async def get_all_active_sessions() -> list[dict]:
    """Get all non-completed sessions, sorted by status priority."""
    db = await get_db()
    cursor = await db.execute("""
        SELECT * FROM sessions
        WHERE status != 'completed'
          AND id NOT LIKE 'agent-%'
        ORDER BY
            CASE status
                WHEN 'waiting' THEN 1
                WHEN 'working' THEN 2
                WHEN 'idle' THEN 3
                WHEN 'stale' THEN 4
                ELSE 5
            END,
            last_activity_at DESC
    """)
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_all_sessions(limit: int = 50, offset: int = 0) -> list[dict]:
    """Get all sessions with pagination, most recent first."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM sessions ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (limit, offset),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_completed_sessions(limit: int = 50, offset: int = 0) -> list[dict]:
    """Get completed sessions for history view."""
    db = await get_db()
    cursor = await db.execute(
        """SELECT * FROM sessions
           WHERE status = 'completed'
           ORDER BY ended_at DESC
           LIMIT ? OFFSET ?""",
        (limit, offset),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


# --- Events ---

async def add_event(
    session_id: str,
    event_type: str,
    tool_name: str | None = None,
    payload: dict | None = None,
) -> int:
    """Add an event to the events table. Returns the event ID."""
    db = await get_db()
    payload_json = json.dumps(payload) if payload else None
    cursor = await db.execute(
        """INSERT INTO events (session_id, event_type, tool_name, payload)
           VALUES (?, ?, ?, ?)""",
        (session_id, event_type, tool_name, payload_json),
    )
    await db.commit()
    return cursor.lastrowid


async def get_session_events(session_id: str, limit: int = 100) -> list[dict]:
    """Get events for a session."""
    db = await get_db()
    cursor = await db.execute(
        """SELECT * FROM events
           WHERE session_id = ?
           ORDER BY created_at DESC
           LIMIT ?""",
        (session_id, limit),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


# --- Transcripts ---

async def add_transcript(
    session_id: str,
    role: str,
    content: str,
    source_file: str | None = None,
    token_count: int | None = None,
    timestamp: str | None = None,
) -> int:
    """Add a transcript entry and update FTS index."""
    db = await get_db()
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    cursor = await db.execute(
        """INSERT INTO transcripts (session_id, source_file, role, content, token_count, timestamp)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (session_id, source_file, role, content, token_count, ts),
    )
    rowid = cursor.lastrowid

    # Update FTS index
    await db.execute(
        "INSERT INTO transcripts_fts (rowid, content) VALUES (?, ?)",
        (rowid, content),
    )
    await db.commit()
    return rowid


async def get_session_transcripts(
    session_id: str, limit: int = 200, offset: int = 0
) -> list[dict]:
    """Get the latest N transcripts for a session, returned in chronological order."""
    db = await get_db()
    # Subquery gets the latest `limit` rows, outer query re-orders ASC for display
    cursor = await db.execute(
        """SELECT * FROM (
               SELECT * FROM transcripts
               WHERE session_id = ?
               ORDER BY id DESC
               LIMIT ? OFFSET ?
           ) sub ORDER BY id ASC""",
        (session_id, limit, offset),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def search_transcripts(query: str, limit: int = 50) -> list[dict]:
    """Full-text search across transcripts."""
    db = await get_db()
    cursor = await db.execute(
        """SELECT t.*, highlight(transcripts_fts, 0, '<mark>', '</mark>') as highlighted
           FROM transcripts_fts fts
           JOIN transcripts t ON t.id = fts.rowid
           WHERE transcripts_fts MATCH ?
           ORDER BY rank
           LIMIT ?""",
        (query, limit),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


# --- Analytics ---

async def get_analytics_summary() -> dict:
    """Get overall analytics summary."""
    db = await get_db()

    cursor = await db.execute("""
        SELECT
            COUNT(*) as total_sessions,
            SUM(CASE WHEN status != 'completed' THEN 1 ELSE 0 END) as active_sessions,
            SUM(cost_usd) as total_cost,
            SUM(input_tokens) as total_input_tokens,
            SUM(output_tokens) as total_output_tokens,
            SUM(cache_tokens) as total_cache_tokens
        FROM sessions
    """)
    row = await cursor.fetchone()
    summary = dict(row)

    # Today's cost
    cursor = await db.execute("""
        SELECT COALESCE(SUM(cost_usd), 0) as today_cost
        FROM sessions
        WHERE date(created_at) = date('now')
    """)
    today = await cursor.fetchone()
    summary["today_cost"] = today["today_cost"]

    return summary


async def get_analytics_daily(days: int = 30) -> list[dict]:
    """Get daily breakdown of usage."""
    db = await get_db()
    cursor = await db.execute(
        """SELECT
            date(created_at) as day,
            COUNT(*) as session_count,
            COALESCE(SUM(cost_usd), 0) as cost,
            COALESCE(SUM(input_tokens), 0) as input_tokens,
            COALESCE(SUM(output_tokens), 0) as output_tokens
        FROM sessions
        WHERE created_at >= datetime('now', ?)
        GROUP BY date(created_at)
        ORDER BY day DESC""",
        (f"-{days} days",),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


# --- Settings ---

async def get_setting(key: str) -> str | None:
    """Get a single setting value by key."""
    db = await get_db()
    cursor = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = await cursor.fetchone()
    return row["value"] if row else None


async def set_setting(key: str, value: str) -> None:
    """Set a setting value (insert or replace)."""
    db = await get_db()
    await db.execute(
        "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
        (key, value),
    )
    await db.commit()


async def get_all_settings() -> dict[str, str]:
    """Get all settings as a key-value dict."""
    db = await get_db()
    cursor = await db.execute("SELECT key, value FROM settings")
    rows = await cursor.fetchall()
    return {row["key"]: row["value"] for row in rows}
