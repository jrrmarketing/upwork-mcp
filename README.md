# upwork-mcp (JRR setup)

Local MCP for Upwork job discovery, fit/reachability screening, price and proof decisions,
proposal preparation, messages, invitations, maintenance, contracts, and bidding reports. It
uses **browser automation** through the owner's Chrome session, not the Upwork GraphQL API.

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

Codex, Cursor, and Claude MCP configs point at `scripts/mcp-server.sh`, so Chrome is checked
before the server starts.

## Use in Cursor

Examples:

- “Search Upwork for Google Ads jobs posted in the last 3 days”
- “Review Best Matches and Most Recent, then rank only realistic JRR opportunities”
- “Screen this job and explain the price, proof, and boost decision”
- “Prepare this proposal for approval without submitting it”
- “Show my Upwork proposals”
- “Get details for this job: https://www.upwork.com/jobs/~…”

Read-only tools may inspect live Upwork. Proposals, messages, withdrawals, and invitation
declines follow prepare -> exact owner approval -> one-time commit -> owner-system readback.
No tool may infer approval or Connect spend from a recommendation.

## Docs

- `docs/env.md` — session paths and CLI commands
- `docs/management.md` — screening, pricing, proof, boosts, maintenance, approval, and learning policy
- `docs/proposal-automation.md` — current Upwork form mechanics

## Verification

```bash
uv lock --check
uv sync --frozen --group dev
uv run ruff check src tests
uv run mypy src/upwork_mcp
uv run pytest -q
```

The default suite is offline. Owner-account checks are read-only and opt-in:

```bash
UPWORK_MCP_LIVE_TEST=1 uv run pytest -q -m upwork_live_readonly
```
