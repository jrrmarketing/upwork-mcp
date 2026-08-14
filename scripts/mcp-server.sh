#!/bin/bash
# Cursor MCP entrypoint: ensure background Chrome is up, then start upwork-mcp.
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
"${ROOT}/scripts/start-chrome-daemon.sh"
cd "$ROOT"
exec uv run upwork-mcp "$@"
