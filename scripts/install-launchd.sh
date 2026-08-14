#!/bin/bash
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AGENTS_DIR="${HOME}/Library/LaunchAgents"

chmod +x "${ROOT}/scripts/"*.sh

mkdir -p "$AGENTS_DIR" "${HOME}/.upwork-mcp/logs"
chmod 700 "${HOME}/.upwork-mcp" "${HOME}/.upwork-mcp/logs"
for log in launchd.err.log launchd.out.log health-launchd.err.log health-launchd.out.log health-check.log chrome-daemon.log; do
  touch "${HOME}/.upwork-mcp/logs/${log}"
  chmod 600 "${HOME}/.upwork-mcp/logs/${log}"
done

for label in com.jrr.upwork-chrome com.jrr.upwork-health; do
  plist="${ROOT}/scripts/${label}.plist"
  dest="${AGENTS_DIR}/${label}.plist"
  cp "$plist" "$dest"
  chmod 600 "$dest"
  launchctl bootout "gui/$(id -u)" "$dest" 2>/dev/null || true
  # A disabled label cannot be bootstrapped; re-enable it first.
  launchctl enable "gui/$(id -u)/${label}" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$dest"
  echo "Loaded ${label}"
done

"${ROOT}/scripts/start-chrome-daemon.sh"
echo "Done. Chrome daemon runs in background; session checks hourly."
