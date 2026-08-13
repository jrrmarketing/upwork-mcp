#!/bin/bash
# Keep the dedicated Upwork Chrome daemon available without owning daily Chrome.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CDP_URL="http://127.0.0.1:9222/json/version"

while true; do
  if ! curl -sf "$CDP_URL" >/dev/null 2>&1; then
    "${ROOT}/scripts/start-chrome-daemon.sh" || true
  fi
  sleep 30
done
