#!/bin/bash
# Remove the retired Upwork Chrome auto-launch and hourly browser-check agents.
set -euo pipefail
umask 077

USER_DOMAIN="gui/$(id -u)"
AGENTS_DIR="${HOME}/Library/LaunchAgents"
LEGACY_PROFILE="${HOME}/.upwork-mcp/chrome-profile"

for label in com.jrr.upwork-chrome com.jrr.upwork-health; do
  plist="${AGENTS_DIR}/${label}.plist"
  launchctl bootout "${USER_DOMAIN}" "${plist}" 2>/dev/null || true
  launchctl disable "${USER_DOMAIN}/${label}" 2>/dev/null || true
  if [ -f "${plist}" ]; then
    rm "${plist}"
  fi
done

while read -r pid command; do
  if [[ "${command}" == *"--user-data-dir=${LEGACY_PROFILE}"* ]]; then
    kill "${pid}" 2>/dev/null || true
  fi
done < <(ps ax -o pid=,command=)

echo "Removed legacy Upwork browser launch agents."
