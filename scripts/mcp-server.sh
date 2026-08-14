#!/bin/bash
# MCP entrypoint. Browser access is attach-only and never launches Chrome.
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
exec uv run upwork-mcp "$@"
