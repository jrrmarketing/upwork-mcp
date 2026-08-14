#!/bin/bash
# Keep the dedicated Upwork Chrome daemon available without owning daily Chrome.
set -u
umask 077

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
while true; do
  if ! (cd "$ROOT" && uv run python -m upwork_mcp.browser.lifecycle status >/dev/null 2>&1); then
    "${ROOT}/scripts/start-chrome-daemon.sh" || true
  fi
  sleep 30
done
