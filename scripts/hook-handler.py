#!/usr/bin/env python3
"""Hook handler script called by Claude Code hooks.

Reads event JSON from stdin and POSTs it to the command center server.
Must NEVER block Claude Code — exits silently on any error.
"""

import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_PORT = 4700
TIMEOUT = 5


def _get_server_url() -> str:
    """Determine server URL from env var, --port arg, or default."""
    if "CCCC_SERVER_URL" in os.environ:
        return os.environ["CCCC_SERVER_URL"]
    # Check for --port arg
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--port" and i < len(sys.argv) - 1:
            return f"http://localhost:{sys.argv[i + 1]}/api/hooks"
    return f"http://localhost:{DEFAULT_PORT}/api/hooks"


def main():
    try:
        server_url = _get_server_url()

        # Read event data from stdin
        input_data = sys.stdin.read()
        if not input_data.strip():
            return

        event_data = json.loads(input_data)

        # Add event type from command-line args if provided
        for i, arg in enumerate(sys.argv[1:], 1):
            if arg == "--event" and i < len(sys.argv) - 1:
                event_data["event_type"] = sys.argv[i + 1]
                break

        # POST to the server
        req = urllib.request.Request(
            server_url,
            data=json.dumps(event_data).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=TIMEOUT)

    except (json.JSONDecodeError, urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError, Exception):
        # Never block Claude Code — exit silently on any error
        pass


if __name__ == "__main__":
    main()
