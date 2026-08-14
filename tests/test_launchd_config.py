import plistlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "filename",
    (
        "com.jrr.upwork-chrome.plist",
        "com.jrr.upwork-health.plist",
    ),
)
def test_launchd_path_includes_macos_system_tools(filename: str) -> None:
    data = plistlib.loads((ROOT / "scripts" / filename).read_bytes())
    configured_path = data["EnvironmentVariables"]["PATH"].split(":")

    assert "/usr/sbin" in configured_path
    assert "/sbin" in configured_path
