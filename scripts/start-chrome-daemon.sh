#!/bin/bash
# Start the exact dedicated Chrome profile under the shared browser-operation lock.
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="${UPWORK_MCP_STATE_DIR:-${HOME}/.upwork-mcp}"
PROFILE_DIR="${STATE_DIR}/chrome-profile"
LOG_DIR="${STATE_DIR}/logs"
LOG_FILE="${LOG_DIR}/chrome-daemon.log"

mkdir -p "$PROFILE_DIR" "$LOG_DIR"
chmod 700 "$STATE_DIR" "$PROFILE_DIR" "$LOG_DIR"
touch "$LOG_FILE"
chmod 600 "$LOG_FILE"

cd "$ROOT"
uv run python -m upwork_mcp.browser.lifecycle ensure
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Dedicated Chrome verified on port 9222" >>"$LOG_FILE"
