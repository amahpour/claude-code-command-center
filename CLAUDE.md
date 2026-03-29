# Claude Code Command Center

## Project Overview
Web-based dashboard for monitoring and managing multiple Claude Code sessions.

## Tech Stack
- Backend: Python 3.12+ / FastAPI / uvicorn / aiosqlite / websockets / pyte
- Frontend: Vanilla HTML/CSS/JS with xterm.js (CDN)
- Database: SQLite with FTS5
- Terminal: tmux + PTY
- Testing: pytest + pytest-asyncio + playwright

## Running
```bash
source .venv/bin/activate
uvicorn server.main:app --port 3000 --reload
```

## Testing
```bash
source .venv/bin/activate
pytest
```

## Project Structure
- `server/` — FastAPI backend
- `public/` — Static frontend files
- `scripts/` — Hook installation scripts
- `tests/` — Unit and e2e tests
