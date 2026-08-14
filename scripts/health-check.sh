#!/bin/bash
# Check an already attached browser session without launching Chrome.
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${HOME}/.upwork-mcp/logs"
LOG_FILE="${LOG_DIR}/health-check.log"

mkdir -p "$LOG_DIR"
chmod 700 "${HOME}/.upwork-mcp" "$LOG_DIR"
touch "$LOG_FILE"
chmod 600 "$LOG_FILE"

if (cd "$ROOT" && uv run upwork-mcp --check >/dev/null 2>&1); then
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) OK session valid" >>"$LOG_FILE"
  exit 0
fi

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) FAIL no attached valid session" >>"$LOG_FILE"
exit 1
