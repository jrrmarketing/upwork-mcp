# upwork-mcp (JRR setup)

Local MCP for Upwork — search jobs, proposals, messages, contracts. Uses **browser automation** (your Chrome session), not the Upwork GraphQL API.

Upstream: [vanooo/upwork-mcp](https://github.com/vanooo/upwork-mcp)

## Why this instead of jrrsales locally?

JRR Sales Hub needs Lovable Google auth to run in the browser. This MCP talks to Upwork directly from Cursor once you log in once — no CRM login, no `UPWORK_CLIENT_ID` / `UPWORK_CLIENT_SECRET`.

## Setup (one time)

```bash
cd ~/Projects/upwork-mcp
uv sync
uv run upwork-mcp --login    # sign in to Upwork in the opened browser
```

## Background Chrome (automatic)

Three layers — you shouldn't need to open Chrome manually:

1. **Mac login** — `com.jrr.upwork-chrome` launchd agent (off-screen Chrome on port 9222)
2. **Cursor session start** — `~/.cursor/hooks.json` runs `start-chrome-daemon.sh`
3. **MCP startup** — `scripts/mcp-server.sh` ensures Chrome before `upwork-mcp` starts

Install launchd once:

```bash
cd ~/Projects/upwork-mcp && ./scripts/install-launchd.sh
```

Cursor MCP config (`~/.cursor/mcp.json`) already points at `scripts/mcp-server.sh`.

## Use in Cursor

Examples:

- “Search Upwork for Google Ads jobs posted in the last 3 days”
- “Show my Upwork proposals”
- “Get details for this job: https://www.upwork.com/jobs/~…”

See upstream README for the full tool list.

## Docs

- `docs/env.md` — session paths and CLI commands
