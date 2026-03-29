#!/usr/bin/env python3
"""Hook handler script called by Claude Code hooks.

Reads event JSON from stdin and POSTs it to the command center server.
Must NEVER block Claude Code — exits silently on any error.
"""

import json
import sys
import urllib.request
import urllib.error

SERVER_URL = "http://localhost:3000/api/hooks"
TIMEOUT = 5


def main():
    try:
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
            SERVER_URL,
            data=json.dumps(event_data).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=TIMEOUT)

    except (json.JSONDecodeError, urllib.error.URLError, urllib.error.HTTPError,
            OSError, TimeoutError, Exception):
        # Never block Claude Code — exit silently on any error
        pass


if __name__ == "__main__":
    main()
