"""Authentication flow for Upwork using CDP."""

import asyncio
import shutil
from typing import Any

from .client import (
    CDP_PORT,
    PROFILE_DIR,
    UpworkBrowser,
    find_chrome,
    is_chrome_running_with_debug,
    start_chrome_with_debug,
)

VISIBLE_LOGIN_BOUNDS: dict[str, int | str] = {
    "left": 80,
    "top": 80,
    "width": 1400,
    "height": 1000,
    "windowState": "normal",
}
BACKGROUND_BOUNDS: dict[str, int | str] = {
    "left": 9999,
    "top": 9999,
    "width": 1,
    "height": 1,
    "windowState": "normal",
}


async def _set_window_bounds(page: Any, bounds: dict[str, int | str]) -> None:
    """Move the daemon window without closing or replacing the owner profile."""
    session = await page.context.new_cdp_session(page)
    try:
        window = await session.send("Browser.getWindowForTarget")
        await session.send(
            "Browser.setWindowBounds",
            {"windowId": window["windowId"], "bounds": bounds},
        )
    finally:
        await session.detach()


async def login_interactive(timeout_minutes: int = 5):
    """Open Chrome for manual login to Upwork.

    Uses CDP to connect to a real Chrome instance, avoiding
    the "controlled by automated test software" detection.

    Args:
        timeout_minutes: How long to wait for user to complete login
    """
    print("=" * 60)
    print("UPWORK LOGIN (CDP Mode)")
    print("=" * 60)
    print()

    # Ensure Chrome is running with debug port
    if not is_chrome_running_with_debug():
        chrome_path = find_chrome()
        if not chrome_path:
            print("ERROR: Chrome not found. Please install Google Chrome.")
            return

        print("Starting Chrome...")
        if not start_chrome_with_debug():
            print("ERROR: Could not start Chrome with debug port.")
            print("Please start Chrome manually with:")
            print(f'  "{chrome_path}" --remote-debugging-port={CDP_PORT}')
            return

        await asyncio.sleep(2)

    browser = UpworkBrowser(timeout=60000)

    try:
        page = await browser.start()

        await _set_window_bounds(page, VISIBLE_LOGIN_BOUNDS)
        await page.bring_to_front()

        print(f"Chrome connected! You have {timeout_minutes} minutes to log in.")
        print()
        print("Steps:")
        print("  1. Open the HeyLogin Chrome extension on the Upwork page")
        print("  2. Select the exact saved Upwork login for Josiah")
        print("  3. Let HeyLogin fill the username, password, and TOTP if requested")
        print("  4. Complete a Cloudflare challenge only if one appears")
        print("  5. Wait until you see the Upwork freelancer dashboard")
        print()
        print("Your session will be saved automatically.")
        print("=" * 60)

        await page.goto("https://www.upwork.com/ab/account-security/login")

        # Wait for successful login - user should be redirected to dashboard
        start_time = asyncio.get_event_loop().time()
        timeout_seconds = timeout_minutes * 60

        while True:
            await asyncio.sleep(3)

            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout_seconds:
                print()
                print("Timeout reached.")
                break

            try:
                current_url = page.url
                title = await page.title()

                # Check if logged in (on dashboard)
                if "/nx/" in current_url and "login" not in current_url.lower():
                    if "moment" not in title.lower():
                        print()
                        print("Login successful!")
                        print(f"Session saved to {PROFILE_DIR}")
                        print()
                        print("You can now use the Upwork MCP server.")
                        break
            except Exception:
                pass  # Page might be navigating

    except Exception as e:
        print()
        print(f"Error: {e}")
        print()
        print("Please try again with: uv run upwork-mcp --login")

    finally:
        if browser._page is not None:
            try:
                await _set_window_bounds(browser._page, BACKGROUND_BOUNDS)
            except Exception:
                pass
        await browser.close()


async def check_session() -> bool:
    """Check if existing session is valid."""
    if not is_chrome_running_with_debug():
        # Start Chrome to check session
        start_chrome_with_debug()
        await asyncio.sleep(2)

    browser = UpworkBrowser()
    try:
        await browser.start()
        return await browser.is_logged_in()
    except Exception:
        return False
    finally:
        await browser.close()


async def logout():
    """Clear saved session."""
    if PROFILE_DIR.exists():
        shutil.rmtree(PROFILE_DIR)
        print("Session cleared.")
    else:
        print("No session to clear.")
