#!/usr/bin/env bash
# Starts the backend, which also serves the frontend at http://127.0.0.1:8000
set -euo pipefail
cd "$(dirname "$0")/backend"

port="${PORT:-$(grep -sE '^PORT=' .env | cut -d= -f2 || true)}"
port="${port:-8000}"
if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $port is already in use - the app is probably already running at http://127.0.0.1:$port"
  echo "Stop it with Ctrl+C in the terminal where it runs, or find it with: lsof -nP -iTCP:$port -sTCP:LISTEN"
  exit 1
fi

# --inexact keeps optional packages (the Von router) installed by `uv sync --extra router`.
exec uv run --inexact python -m app "$@"
