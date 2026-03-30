#!/bin/bash
# Setup script — installs Claude Code hooks for the Command Center
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HANDLER_PATH="$SCRIPT_DIR/hook-handler.py"
SETTINGS_FILE="$HOME/.claude/settings.json"
PORT="${1:-4700}"

if [ ! -f "$HANDLER_PATH" ]; then
  echo "Error: hook-handler.py not found at $HANDLER_PATH"
  exit 1
fi

echo "Installing Claude Code Command Center hooks (port $PORT)..."

python3 -c "
import json, os

handler_path = '$HANDLER_PATH'
settings_path = os.path.expanduser('$SETTINGS_FILE')
port = '$PORT'

# Read existing settings
settings = {}
try:
    with open(settings_path) as f:
        settings = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    pass

if 'hooks' not in settings:
    settings['hooks'] = {}

events = [
    'SessionStart', 'PreToolUse', 'PostToolUse', 'Stop',
    'SubagentStart', 'SubagentStop', 'SessionEnd', 'Notification'
]

updated = []

for event in events:
    if event not in settings['hooks']:
        settings['hooks'][event] = []

    expected_cmd = f'python3 {handler_path} --port {port} --event {event}'

    # Find and update existing hook, or add new one
    found = False
    for entry in settings['hooks'][event]:
        hooks_list = entry.get('hooks', [])
        for h in hooks_list:
            if 'hook-handler.py' in h.get('command', ''):
                if h['command'] != expected_cmd:
                    h['command'] = expected_cmd
                    updated.append(event)
                found = True
                break
        if found:
            break

    if not found:
        settings['hooks'][event].append({
            'matcher': '',
            'hooks': [{
                'type': 'command',
                'command': expected_cmd
            }]
        })
        updated.append(event)

os.makedirs(os.path.dirname(settings_path), exist_ok=True)
with open(settings_path, 'w') as f:
    json.dump(settings, f, indent=2)

if updated:
    print(f'Hooks updated for: {\", \".join(updated)}')
else:
    print('All hooks already up to date.')
print('Done!')
"
