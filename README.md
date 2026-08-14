# upwork-mcp (JRR setup)

Local MCP for Upwork job discovery, fit/reachability screening, price and proof decisions,
proposal preparation, messages, invitations, maintenance, contracts, and bidding reports. It
uses **attach-only browser automation** through an existing owner Chrome window, not the Upwork
GraphQL API. It never launches Chrome or creates a separate browser window.

Upstream: [vanooo/upwork-mcp](https://github.com/vanooo/upwork-mcp)

## Why this instead of jrrsales locally?

JRR Sales Hub needs Lovable Google auth to run in the browser. This MCP talks to Upwork directly from Cursor once you log in once — no CRM login, no `UPWORK_CLIENT_ID` / `UPWORK_CLIENT_SECRET`.

## Setup (one time)

```bash
cd ~/Projects/upwork-mcp
uv sync
uv run upwork-mcp --login    # opens a tab only when an existing browser endpoint is attached
```

## Existing-window browser policy

The MCP never starts Chrome, installs launch agents, changes window bounds, or creates a browser
context. When an explicitly configured local CDP endpoint is already available, it may reuse an
existing Upwork tab or open a new tab inside that existing browser context. If no safe endpoint or
existing context is available, browser-dependent tools fail closed without opening anything.

`UPWORK_MCP_CDP_URL` defaults to `http://127.0.0.1:9222` and accepts loopback endpoints only. The
owner or calling browser integration must expose that endpoint from the Chrome window already in
use. Starting `scripts/mcp-server.sh` alone never starts a browser.

Remove the retired auto-launch agents from older installations once:

```bash
cd ~/Projects/upwork-mcp && ./scripts/uninstall-legacy-launchd.sh
```

Each MCP client should be registered with `scripts/mcp-server.sh` as its command. Start from
`mcp-config.example.json` and use the canonical checkout's absolute path. Registration is a local
deployment step; repository tests do not prove that a browser endpoint or a particular client is
already configured.

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
No tool may infer approval or Connect spend from a recommendation. Positive boosts remain
recommendation-only until Upwork's first Submit transition can be proven non-consequential.

## Docs

- `docs/env.md` — session paths and CLI commands
- `docs/management.md` — screening, pricing, proof, boosts, maintenance, approval, and learning policy
- `docs/proposal-automation.md` — current Upwork form mechanics

## Verification

```bash
./scripts/verify.sh
```

The same locked verification runs on every pull request and push to `main`.

The default suite is offline. Owner-account checks are read-only and opt-in:

```bash
UPWORK_MCP_LIVE_TEST=1 uv run pytest -q -m upwork_live_readonly
```
