"""Regression checks for the no-new-browser-window policy."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_mcp_entrypoint_never_starts_chrome() -> None:
    entrypoint = (ROOT / "scripts" / "mcp-server.sh").read_text(encoding="utf-8")

    assert "start-chrome" not in entrypoint
    assert "launchctl" not in entrypoint
    assert "exec uv run upwork-mcp" in entrypoint


def test_legacy_auto_launch_assets_are_removed() -> None:
    retired = (
        "start-chrome-daemon.sh",
        "stop-chrome-daemon.sh",
        "chrome-daemon-supervisor.sh",
        "install-launchd.sh",
        "com.jrr.upwork-chrome.plist",
        "com.jrr.upwork-health.plist",
    )

    assert all(not (ROOT / "scripts" / name).exists() for name in retired)


def test_manual_health_check_never_starts_browser_or_posts_notification() -> None:
    health = (ROOT / "scripts" / "health-check.sh").read_text(encoding="utf-8")

    assert "start-chrome" not in health
    assert "osascript" not in health


def test_legacy_uninstaller_targets_only_known_upwork_agents() -> None:
    uninstaller = (ROOT / "scripts" / "uninstall-legacy-launchd.sh").read_text(encoding="utf-8")

    assert "com.jrr.upwork-chrome" in uninstaller
    assert "com.jrr.upwork-health" in uninstaller
    assert 'CHROME_EXECUTABLE="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"' in uninstaller
    assert '"${command}" == "${CHROME_EXECUTABLE}"*' in uninstaller
    assert "--user-data-dir=${LEGACY_PROFILE}" in uninstaller
    assert "pkill" not in uninstaller
    assert "rm -rf" not in uninstaller
