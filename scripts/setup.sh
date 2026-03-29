#!/bin/bash
# Setup script — installs Claude Code hooks for the Command Center
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HANDLER_PATH="$SCRIPT_DIR/hook-handler.py"
SETTINGS_FILE="$HOME/.claude/settings.json"

if [ ! -f "$HANDLER_PATH" ]; then
  echo "Error: hook-handler.py not found at $HANDLER_PATH"
  exit 1
fi

echo "Installing Claude Code Command Center hooks..."
echo "Handler: $HANDLER_PATH"
echo "Settings: $SETTINGS_FILE"

python3 -c "
import json, os, sys

handler_path = '$HANDLER_PATH'
settings_path = os.path.expanduser('$SETTINGS_FILE')

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

handler_cmd = f'python3 {handler_path}'
installed = []

for event in events:
    if event not in settings['hooks']:
        settings['hooks'][event] = []

    # Check if already installed
    already = False
    for entry in settings['hooks'][event]:
        hooks_list = entry.get('hooks', [])
        for h in hooks_list:
            if 'hook-handler.py' in h.get('command', ''):
                already = True
                break
        if already:
            break

    if not already:
        settings['hooks'][event].append({
            'matcher': '',
            'hooks': [{
                'type': 'command',
                'command': f'{handler_cmd} --event {event}'
            }]
        })
        installed.append(event)

os.makedirs(os.path.dirname(settings_path), exist_ok=True)
with open(settings_path, 'w') as f:
    json.dump(settings, f, indent=2)

if installed:
    print(f'Hooks installed for: {\", \".join(installed)}')
else:
    print('All hooks already installed.')
print('Done!')
"
