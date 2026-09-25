#!/bin/bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

echo "=== Starting Mac Remote Cam Server ==="
# The server starts `caffeinate` itself (keeps the Mac awake while it runs)
# and reports its real status on the dashboard.
./.venv/bin/python main.py
