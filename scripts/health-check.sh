#!/bin/bash
# Ensure Chrome is up and Upwork session is still valid.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${HOME}/.upwork-mcp/logs"
LOG_FILE="${LOG_DIR}/health-check.log"

mkdir -p "$LOG_DIR"

"${ROOT}/scripts/start-chrome-daemon.sh"

if (cd "$ROOT" && uv run upwork-mcp --check >/dev/null 2>&1); then
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) OK session valid" >>"$LOG_FILE"
  exit 0
fi

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) FAIL session invalid — re-login required" >>"$LOG_FILE"
osascript -e 'display notification "Run: cd ~/Projects/upwork-mcp && uv run upwork-mcp --login" with title "Upwork MCP session expired"' 2>/dev/null || true
exit 1
