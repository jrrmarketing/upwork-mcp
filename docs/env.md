# upwork-mcp — Environment

No API keys. Auth is a saved Chrome browser session.

| Item | Location |
|---|---|
| Session profile | `~/.upwork-mcp/chrome-profile/` (or `~/.upwork-mcp/profile/` per upstream README) |
| MCP configs | Codex, Cursor, and Claude → `scripts/mcp-server.sh` |
| Expected freelancer slug | `UPWORK_FREELANCER_PROFILE_SLUG` (defaults to `josiahroche2`) |

## One-time login

```bash
cd ~/Projects/upwork-mcp
uv run upwork-mcp --login
```

Complete Upwork login in the browser window with the saved HeyLogin entry and TOTP workflow.
Never paste, print, or store credentials in this repository. If HeyLogin needs device approval,
approve that single unlock and resume the original login.

The health check also verifies the expected freelancer profile and find-work dashboard. A valid
client-side Upwork session or a different freelancer profile therefore fails closed.

## Check session

```bash
uv run upwork-mcp --check
```

## Clear session

```bash
uv run upwork-mcp --logout
```

Then reload the Upwork MCP in the active client.

## Background Chrome (no window to babysit)

Upwork blocks **headless** Chrome. We run a **1×1px off-screen** window instead — you can close the visible Upwork tab; launchd keeps the browser process alive.

```bash
cd ~/Projects/upwork-mcp
chmod +x scripts/*.sh
./scripts/install-launchd.sh
```

This installs:

| LaunchAgent | Purpose |
|---|---|
| `com.jrr.upwork-chrome` | Starts at login, restarts if Chrome dies |
| `com.jrr.upwork-health` | Hourly session check + macOS notification if expired |

Manual controls:

```bash
./scripts/start-chrome-daemon.sh   # start if not running
./scripts/stop-chrome-daemon.sh    # stop background Chrome
./scripts/health-check.sh          # verify session now
```

Logs: `~/.upwork-mcp/logs/`

### Activepieces?

Cloud Activepieces **cannot** reach `localhost:9222` on your Mac, so it can't drive this MCP directly. Use **launchd** for the daemon; use Activepieces only for **alerts** (e.g. webhook when `health-check.sh` fails) if you self-host a flow or run a local script on a schedule.

Re-login (~once every few weeks): `uv run upwork-mcp --login` when health check notifies you.
