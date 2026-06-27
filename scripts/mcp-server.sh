#!/bin/bash
# Cursor MCP entrypoint: ensure background Chrome is up, then start upwork-mcp.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
"${ROOT}/scripts/start-chrome-daemon.sh" || true
cd "$ROOT"
exec uv run upwork-mcp "$@"
