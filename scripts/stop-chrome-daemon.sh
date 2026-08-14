#!/bin/bash
# Stop only the exact dedicated Upwork Chrome instance.
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
uv run python -m upwork_mcp.browser.lifecycle stop
echo "Stopped dedicated Upwork Chrome."
