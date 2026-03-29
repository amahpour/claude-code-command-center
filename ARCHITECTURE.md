# Architecture

## How It Works (The 30-Second Version)

Claude Code has a **hooks system** — you can register shell commands that run every time Claude does something (starts a session, uses a tool, finishes responding, etc.). We abuse this to spy on every Claude Code session you run, anywhere on your machine, and pipe the data into a web dashboard.

```
You run Claude Code in any terminal
        |
        v
Claude Code fires a hook event (e.g. "I just used the Read tool")
        |
        v
hook-handler.py receives the event JSON on stdin
        |
        v
hook-handler.py POSTs it to http://localhost:3000/api/hooks
        |
        v
The server updates the SQLite DB and broadcasts via WebSocket
        |
        v
Your browser dashboard updates in real-time
```

## What Got Installed

### 1. Hooks in `~/.claude/settings.json`

The setup script added entries like this for 8 event types:

```json
{
  "hooks": {
    "SessionStart": [{ "matcher": "", "hooks": [{ "type": "command", "command": "python3 /path/to/hook-handler.py --event SessionStart" }] }],
    "PreToolUse":   [{ ... same pattern ... }],
    "PostToolUse":  [{ ... }],
    "Stop":         [{ ... }],
    "SubagentStart":[{ ... }],
    "SubagentStop": [{ ... }],
    "SessionEnd":   [{ ... }],
    "Notification": [{ ... }]
  }
}
```

**Every** Claude Code session on your machine will now fire these hooks. The `matcher: ""` means "match everything" — no filtering.

### 2. `scripts/hook-handler.py` — The Bridge

This is a tiny script with **zero dependencies** (only stdlib `urllib` and `json`). Claude Code pipes event JSON into its stdin. It POSTs that JSON to the server.

Critical design constraint: **it must never block Claude Code.** If the server is down, it silently exits. 5-second timeout on the HTTP request. No retries. No logging. If it fails, Claude Code never notices.

### 3. The Server (`server/`)

A FastAPI app running on port 3000. It does four things:

| Component | File | What It Does |
|-----------|------|-------------|
| **Hook Processor** | `server/hooks.py` | Receives hook events, updates session state in SQLite (status, cost, tokens, context usage) |
| **JSONL Watcher** | `server/watcher.py` | Watches `~/.claude/projects/` for `.jsonl` files (Claude Code's conversation logs), parses them, indexes transcripts for full-text search |
| **Stale Checker** | `server/hooks.py` | Background task that runs every 60s, marks sessions with no activity for 5+ minutes as "stale" |

### 4. The Database (`~/.claude-command-center/data.db`)

SQLite with three tables:

- **`sessions`** — One row per Claude Code session. Tracks project path, git branch, model, status, cost, token counts, context usage, timestamps.
- **`events`** — Every hook event received, with full JSON payload. Foreign key to sessions.
- **`transcripts`** — Parsed conversation entries from JSONL files. Has a **FTS5 virtual table** for full-text search.

### 5. The Frontend (`public/`)

Vanilla HTML/CSS/JS. No build step. No npm. No framework. Just files served by FastAPI's `StaticFiles`.

| File | What It Does |
|------|-------------|
| `js/app.js` | Opens a WebSocket to `/ws/dashboard`, manages navigation between views |
| `js/dashboard.js` | Renders session cards in a CSS Grid. Cards show status (color-coded dot), project name, model, context bar, cost, duration |
| `js/terminal.js` | Shows a live transcript view when clicking a session card. Polls `/api/sessions/:id/transcript` every 2s for updates. Renders messages with markdown, collapsible tool calls, and tool output |
| `js/history.js` | Table view of past sessions. Search bar queries the FTS5 index via `/api/search` |
| `js/analytics.js` | Canvas-drawn charts (no charting library). Bar chart for daily sessions, donut chart for token breakdown |

## Data Flow for Each Event Type

| Hook Event | What Happens |
|-----------|-------------|
| `SessionStart` | Creates a new session row. Extracts project name from cwd, tries to detect git branch |
| `PreToolUse` | Sets session status to **working** (green dot). Records the tool name |
| `PostToolUse` | Updates `last_activity_at` timestamp |
| `Stop` | Sets status to **idle** (blue dot). Extracts cost/token/context data if present in the payload |
| `Notification` | Sets status to **waiting** (yellow dot). Stores the notification message as the task description |
| `SubagentStart/Stop` | Logged as events, updates activity timestamp |
| `SessionEnd` | Sets status to **completed**. Records end time |

## Session Status Priority (Dashboard Sort Order)

1. **waiting** (yellow) — needs your attention, shown first
2. **working** (green) — actively running tool calls
3. **idle** (blue) — session is open but Claude isn't doing anything
4. **stale** (grey) — no activity for 5+ minutes
5. **completed** (grey) — session ended

## API Endpoints

```
GET  /api/health                    → {"status": "ok"}
GET  /api/sessions                  → Active sessions sorted by status priority
GET  /api/sessions/:id              → Single session + recent events
GET  /api/sessions/:id/transcript   → Full conversation transcript
POST /api/hooks                     → Receives hook events (called by hook-handler.py)
GET  /api/history?limit=50&offset=0 → All sessions with pagination
GET  /api/search?q=query            → Full-text search across all transcripts
GET  /api/analytics/summary         → Total sessions, cost, tokens
GET  /api/analytics/daily?days=30   → Daily breakdown
```

## WebSocket Endpoints

- **`/ws/dashboard`** — Sends `initial_state` on connect, then `session_update` messages whenever a hook event changes session state. All connected browsers get updates.

## What to Know

- **Removing hooks:** `bash scripts/uninstall.sh` cleanly removes only the Command Center entries from `~/.claude/settings.json`, leaving everything else intact.
- **Server down?** Hook handler fails silently. Your Claude Code sessions are completely unaffected.
- **Multiple machines?** Each machine runs its own server + DB. No sync between them.
- **Database location:** `~/.claude-command-center/data.db`. Delete it to reset everything.
- **The JSONL watcher** reads from `~/.claude/projects/` which is where Claude Code stores conversation logs. It tracks file positions so it only reads new lines.
