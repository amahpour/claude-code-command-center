"""End-to-end tests for the dashboard UI using Playwright.

These tests start a real server and use a browser to verify the UI.
Run with: pytest tests/e2e/ -v
"""

import asyncio
import multiprocessing
import time

import pytest

SERVER_PORT = 3001
SERVER_URL = f"http://localhost:{SERVER_PORT}"


def _run_server():
    """Run the server in a separate process."""
    import uvicorn
    from server.main import app
    uvicorn.run(app, host="127.0.0.1", port=SERVER_PORT, log_level="warning")


@pytest.fixture(scope="module")
def server():
    """Start the server as a separate process."""
    proc = multiprocessing.Process(target=_run_server, daemon=True)
    proc.start()
    time.sleep(2)  # Wait for server to be ready
    yield proc
    proc.terminate()
    proc.join(timeout=5)


@pytest.fixture(scope="module")
def pw_browser(server):
    """Launch a Playwright browser for the module."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(pw_browser):
    page = pw_browser.new_page()
    yield page
    page.close()


def test_dashboard_loads(page):
    page.goto(SERVER_URL)
    assert "Claude Code Command Center" in page.title()
    assert page.text_content("h1") == "Claude Code Command Center"


def test_navigation_tabs(page):
    page.goto(SERVER_URL)

    page.click('[data-view="history"]')
    assert page.is_visible("#view-history")

    page.click('[data-view="analytics"]')
    assert page.is_visible("#view-analytics")

    page.click('[data-view="dashboard"]')
    assert page.is_visible("#view-dashboard")


def test_new_session_modal(page):
    page.goto(SERVER_URL)

    page.click("#new-session-btn")
    assert page.is_visible(".modal")

    page.click("#modal-cancel")
    page.wait_for_timeout(200)
    assert "none" in (page.get_attribute("#new-session-modal", "style") or "")


def test_hook_creates_session_card(page):
    page.goto(SERVER_URL)
    page.wait_for_timeout(500)

    # Send hook via fetch
    page.evaluate("""() => {
        return fetch('/api/hooks', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                event_type: 'SessionStart',
                session_id: 'e2e-1',
                cwd: '/tmp/e2e-project',
                model: 'opus'
            })
        }).then(r => r.json());
    }""")

    page.wait_for_timeout(500)
    page.reload()
    page.wait_for_timeout(500)

    assert "e2e-project" in page.text_content("#session-grid")


def test_history_page(page):
    page.goto(SERVER_URL)
    page.click('[data-view="history"]')
    page.wait_for_timeout(500)

    assert page.is_visible("#history-search")
    assert page.is_visible(".history-table")


def test_analytics_page(page):
    page.goto(SERVER_URL)
    page.click('[data-view="analytics"]')
    page.wait_for_timeout(500)

    assert page.is_visible("#analytics-cards")
    assert page.is_visible("#chart-daily")
    assert page.is_visible("#chart-tokens")


def test_session_card_opens_terminal(page):
    page.goto(SERVER_URL)
    page.wait_for_timeout(500)

    card = page.query_selector(".session-card")
    if card:
        card.click()
        page.wait_for_timeout(500)
        assert page.is_visible("#terminal-overlay")

        page.click("#terminal-back")
        page.wait_for_timeout(200)
        assert "none" in (page.get_attribute("#terminal-overlay", "style") or "")


def test_health_api(page):
    response = page.goto(f"{SERVER_URL}/api/health")
    assert response.status == 200
