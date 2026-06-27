#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AGENTS_DIR="${HOME}/Library/LaunchAgents"

chmod +x "${ROOT}/scripts/"*.sh

mkdir -p "$AGENTS_DIR" "${HOME}/.upwork-mcp/logs"

for label in com.jrr.upwork-chrome com.jrr.upwork-health; do
  plist="${ROOT}/scripts/${label}.plist"
  dest="${AGENTS_DIR}/${label}.plist"
  cp "$plist" "$dest"
  launchctl bootout "gui/$(id -u)" "$dest" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$dest"
  launchctl enable "gui/$(id -u)/${label}" 2>/dev/null || true
  echo "Loaded ${label}"
done

"${ROOT}/scripts/start-chrome-daemon.sh"
echo "Done. Chrome daemon runs in background; session checks hourly."
