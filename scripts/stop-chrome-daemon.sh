#!/bin/bash
# Stop the upwork-mcp Chrome instance (does not clear session cookies).
set -euo pipefail

pkill -f "user-data-dir=${HOME}/.upwork-mcp/chrome-profile" 2>/dev/null || true
echo "Stopped upwork-mcp Chrome (if it was running)."
