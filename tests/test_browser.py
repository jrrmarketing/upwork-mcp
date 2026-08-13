"""Tests for browser client."""

import os

import pytest

live_readonly = pytest.mark.skipif(
    os.environ.get("UPWORK_MCP_LIVE_TEST") != "1",
    reason="Set UPWORK_MCP_LIVE_TEST=1 for owner-account read-only browser checks",
)


@pytest.mark.asyncio
async def test_profile_dir_exists():
    """Test that profile directory can be created."""
    from upwork_mcp.browser.client import PROFILE_DIR

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    assert PROFILE_DIR.exists()


@pytest.mark.asyncio
@pytest.mark.upwork_live_readonly
@live_readonly
async def test_browser_launch(browser):
    """Test that browser can launch."""
    page = await browser.start()
    assert page is not None


@pytest.mark.asyncio
@pytest.mark.upwork_live_readonly
@live_readonly
async def test_browser_navigation(browser):
    """Test that browser can navigate."""
    page = await browser.start()
    await page.goto("https://www.upwork.com")
    title = await page.title()
    assert "Upwork" in title or "Work" in title


@pytest.mark.asyncio
@pytest.mark.upwork_live_readonly
@live_readonly
async def test_browser_close(browser):
    """Test that browser closes cleanly."""
    await browser.start()
    await browser.close()
    assert browser._page is None
    assert browser._context is None
