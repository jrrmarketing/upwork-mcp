#!/bin/bash
# Start headful Chrome on CDP port 9222 for upwork-mcp (off-screen, no interaction needed).
set -euo pipefail

CDP_PORT=9222
PROFILE_DIR="${HOME}/.upwork-mcp/chrome-profile"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
LOG_DIR="${HOME}/.upwork-mcp/logs"
LOG_FILE="${LOG_DIR}/chrome-daemon.log"

mkdir -p "$PROFILE_DIR" "$LOG_DIR"

if curl -sf "http://127.0.0.1:${CDP_PORT}/json/version" >/dev/null 2>&1; then
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Chrome already listening on ${CDP_PORT}" >>"$LOG_FILE"
  exit 0
fi

if [[ ! -x "$CHROME" ]]; then
  echo "Google Chrome not found at $CHROME" >&2
  exit 1
fi

# Headless breaks Upwork/Cloudflare. Use a tiny off-screen window instead.
nohup "$CHROME" \
  --remote-debugging-port="${CDP_PORT}" \
  --user-data-dir="${PROFILE_DIR}" \
  --no-first-run \
  --no-default-browser-check \
  --window-size=1,1 \
  --window-position=9999,9999 \
  about:blank >>"$LOG_FILE" 2>&1 &

for _ in $(seq 1 20); do
  if curl -sf "http://127.0.0.1:${CDP_PORT}/json/version" >/dev/null 2>&1; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Chrome started on port ${CDP_PORT}" >>"$LOG_FILE"
    exit 0
  fi
  sleep 0.5
done

echo "Chrome failed to start on port ${CDP_PORT}" >&2
exit 1
