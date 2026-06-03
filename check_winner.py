#!/usr/bin/env python3
import json
from pathlib import Path

# Check raw win event
with open('downloaded_logs/gen9championsvgc2026regma/gen9championsvgc2026regma-2594983461.json') as f:
    data = json.load(f)

log_lines = data['log'].splitlines()
for line in log_lines:
    if '|win|' in line:
        print(f'Raw win line: {line}')
        parts = line.split('|')
        print(f'Winner value: {repr(parts[2] if len(parts) > 2 else "???")}')

# Now check what the parsed log gives us
from showdown_ai import load_showdown_log_json

log = load_showdown_log_json('downloaded_logs/gen9championsvgc2026regma/gen9championsvgc2026regma-2594983461.json')

# Find win event in parsed
for evt in log.events:
    if evt.kind == 'win':
        print(f'\nParsed win event:')
        print(f'  kind: {evt.kind}')
        print(f'  args: {evt.args}')
