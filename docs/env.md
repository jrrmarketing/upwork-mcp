# upwork-mcp — Environment

No API keys. Auth remains in the owner's existing Chrome session.

| Item | Location |
|---|---|
| Browser endpoint | `UPWORK_MCP_CDP_URL` (loopback only; defaults to `http://127.0.0.1:9222`) |
| MCP configs | Codex, Cursor, and Claude → `scripts/mcp-server.sh` |
| Expected freelancer profile identifiers | `UPWORK_FREELANCER_PROFILE_IDENTIFIERS` (comma-separated; defaults to Josiah's public slug and profile ID) |
| Legacy single freelancer slug | `UPWORK_FREELANCER_PROFILE_SLUG` (still supported when the identifiers variable is unset) |

## One-time login

```bash
cd ~/Projects/upwork-mcp
uv run upwork-mcp --login
```

Keep the Upwork sign-in page open, open `https://heylogin.app/` in another tab in the same Chrome
window, and
search the vault for `upwork.com` plus the intended freelancer identity. Use only the exact matched
entry for the username, password, and TOTP; never use the extension popup or reload flow. Never
paste, print, or store credentials in this repository. If HeyLogin needs device approval, approve
that single unlock and resume the original login.

The health check also verifies the expected freelancer profile and find-work dashboard. A valid
client-side Upwork session or a different freelancer profile therefore fails closed.
It opens and closes its own disposable tab and never navigates an existing proposal or message.
All Codex, Cursor, Claude, health, and login operations share one crash-safe local file
lock, so separate MCP processes cannot operate the browser concurrently.

## Check session

```bash
uv run upwork-mcp --check
```

## Clear session

```bash
uv run upwork-mcp --logout
```

The command disconnects Patchright only. It never closes Chrome, deletes the owner's profile, or
changes browser data. Log out from Upwork in the existing Chrome window when an actual account
logout is intended.

## Existing-window attach mode

Upwork MCP is attach-only. It does not launch Chrome, create an off-screen profile, install a
launch agent, resize or reposition a window, or create a new browser context. It can reuse an
existing Upwork tab or create a tab in an existing attached context. A missing endpoint or context
is a normal fail-closed state.

Older releases installed two automatic launch agents. Remove them once:

```bash
cd ~/Projects/upwork-mcp
./scripts/uninstall-legacy-launchd.sh
```

`scripts/mcp-server.sh` now starts only the MCP server. `scripts/health-check.sh` checks only an
already attached session and never starts a browser or posts an automatic notification.

The state, lock, and log paths are owner-only. The endpoint must be a credential-free loopback URL.
Browser operations still prove the exact Upwork freelancer identity before treating the session as
valid, and consequential actions retain their exact target and approval gates.

### Activepieces?

Cloud Activepieces **cannot** reach a loopback browser endpoint on your Mac, so it cannot drive this
MCP directly. Keep browser control local.

Re-login when required: attach the existing browser first, then run `uv run upwork-mcp --login`.
