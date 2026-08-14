"""Authentication flow for Upwork using CDP."""

import asyncio
from typing import Any

from .client import (
    PROFILE_DIR,
    UpworkBrowser,
    clear_saved_session,
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

HEYLOGIN_LOGIN_STEPS: tuple[str, ...] = (
    "Keep the Upwork sign-in page open",
    "Open https://heylogin.app/ in a separate Chrome tab",
    "Search the vault for upwork.com plus the intended freelancer identity",
    "Use only the exact matched entry for the username, password, and TOTP",
    "Return to Upwork and complete a Cloudflare challenge only if one appears",
    "Wait until you see the Upwork freelancer dashboard",
)


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

    browser = UpworkBrowser(timeout=60000)

    try:
        async with browser.disposable_operation() as page:
            await _set_window_bounds(page, VISIBLE_LOGIN_BOUNDS)
            await page.bring_to_front()

            print(f"Chrome connected! You have {timeout_minutes} minutes to log in.")
            print()
            print("Steps:")
            for index, step in enumerate(HEYLOGIN_LOGIN_STEPS, start=1):
                print(f"  {index}. {step}")
            print()
            print("Your session will be saved automatically.")
            print("=" * 60)

            await page.goto("https://www.upwork.com/ab/account-security/login")
            start_time = asyncio.get_event_loop().time()
            timeout_seconds = timeout_minutes * 60

            while True:
                await asyncio.sleep(3)
                if asyncio.get_event_loop().time() - start_time > timeout_seconds:
                    print()
                    print("Timeout reached.")
                    break
                try:
                    current_url = page.url
                    title = await page.title()
                    if "/nx/" in current_url and "login" not in current_url.lower():
                        if "moment" not in title.lower():
                            print()
                            print("Login successful!")
                            print(f"Session saved to {PROFILE_DIR}")
                            print()
                            print("You can now use the Upwork MCP server.")
                            break
                except Exception:
                    pass

            try:
                await _set_window_bounds(page, BACKGROUND_BOUNDS)
            except Exception:
                pass

    except Exception as e:
        print()
        print(f"Error: {e}")
        print()
        print("Please try again with: uv run upwork-mcp --login")

    finally:
        await browser.close()


async def check_session() -> bool:
    """Check the saved session in a dedicated disposable tab."""
    browser = UpworkBrowser()
    try:
        return await browser.is_logged_in_disposable()
    except Exception:
        return False
    finally:
        await browser.close()


async def logout():
    """Stop the dedicated browser and clear its saved session safely."""
    existed = await asyncio.to_thread(clear_saved_session)
    print("Session cleared." if existed else "No session to clear.")
