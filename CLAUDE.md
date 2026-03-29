# Claude Code Command Center

## Project Overview
Web-based dashboard for monitoring and managing multiple Claude Code sessions.

## Tech Stack
- Backend: Python 3.12+ / FastAPI / uvicorn / aiosqlite / websockets
- Frontend: Vanilla HTML/CSS/JS (no build step)
- Database: SQLite with FTS5
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
