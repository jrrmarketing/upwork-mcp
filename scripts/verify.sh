#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

uv lock --check
uv sync --frozen --group dev
uv run ruff check src tests
uv run mypy src/upwork_mcp
uv run pytest -q
uv build

for script in scripts/*.sh; do
  bash -n "$script"
done
