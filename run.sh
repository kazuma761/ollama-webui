#!/usr/bin/env bash
# Starts the backend, which also serves the frontend at http://127.0.0.1:8000
set -euo pipefail
cd "$(dirname "$0")/backend"
exec uv run python -m app "$@"
