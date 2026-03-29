#!/bin/bash
# Uninstall script — removes Command Center hooks from Claude Code settings
set -e

SETTINGS_FILE="$HOME/.claude/settings.json"

echo "Removing Claude Code Command Center hooks..."
echo "Settings: $SETTINGS_FILE"

python3 -c "
import json, os, sys

settings_path = os.path.expanduser('$SETTINGS_FILE')

try:
    with open(settings_path) as f:
        settings = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    print('No settings file found. Nothing to remove.')
    sys.exit(0)

if 'hooks' not in settings:
    print('No hooks found. Nothing to remove.')
    sys.exit(0)

removed = []
events_to_clean = []
for event, entries in list(settings['hooks'].items()):
    original_len = len(entries)
    # Filter out entries that reference hook-handler.py
    settings['hooks'][event] = [
        entry for entry in entries
        if not any(
            'hook-handler.py' in h.get('command', '')
            for h in entry.get('hooks', [])
        )
    ]
    if len(settings['hooks'][event]) < original_len:
        removed.append(event)

    # Mark empty event arrays for cleanup
    if not settings['hooks'][event]:
        events_to_clean.append(event)

for event in events_to_clean:
    del settings['hooks'][event]

# Clean up empty hooks dict
if not settings['hooks']:
    del settings['hooks']

with open(settings_path, 'w') as f:
    json.dump(settings, f, indent=2)

if removed:
    print(f'Hooks removed for: {\", \".join(removed)}')
else:
    print('No Command Center hooks found.')
print('Done!')
"
