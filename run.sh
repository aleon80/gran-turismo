#!/bin/bash
# GT7 Telemetry Dashboard launcher
unset PYTHONHOME
DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$DIR/.venv/bin/python" "$DIR/server.py" "$@"
