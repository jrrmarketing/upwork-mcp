"""Authentication flow for Upwork using CDP."""

import asyncio

from .client import (
    UpworkBrowser,
    close_browser,
)

HEYLOGIN_LOGIN_STEPS: tuple[str, ...] = (
    "Keep the Upwork sign-in page open",
    "Open https://heylogin.app/ in another tab in the same Chrome window",
    "Search the vault for upwork.com plus the intended freelancer identity",
    "Use only the exact matched entry for the username, password, and TOTP",
    "Return to Upwork and complete a Cloudflare challenge only if one appears",
    "Wait until you see the Upwork freelancer dashboard",
)


async def login_interactive(timeout_minutes: int = 5):
    """Open a login tab inside an already attached Chrome window.

    Args:
        timeout_minutes: How long to wait for user to complete login
    """
    print("=" * 60)
    print("UPWORK LOGIN (CDP Mode)")
    print("=" * 60)
    print()

    browser = UpworkBrowser(timeout=60000)
    logged_in = False

    try:
        async with browser.disposable_operation() as page:
            await page.bring_to_front()

            print(f"Existing Chrome connected. You have {timeout_minutes} minutes to log in.")
            print()
            print("Steps:")
            for index, step in enumerate(HEYLOGIN_LOGIN_STEPS, start=1):
                print(f"  {index}. {step}")
            print()
            print("The existing Chrome profile keeps its own session.")
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
                            print()
                            print("You can now use the Upwork MCP server.")
                            logged_in = True
                            break
                except Exception:
                    pass

            if not logged_in:
                raise TimeoutError("Upwork login was not confirmed before the timeout.")

    except Exception as e:
        print()
        print(f"Error: {e}")
        print()
        print("No browser was launched. Attach the existing Chrome window, then try again.")
        raise

    finally:
        await browser.close()


async def check_session() -> bool:
    """Check the existing browser session in a disposable same-window tab."""
    browser = UpworkBrowser()
    try:
        return await browser.is_logged_in_disposable()
    except Exception:
        return False
    finally:
        await browser.close()


async def logout():
    """Disconnect automation without closing Chrome or deleting browser data."""
    await close_browser()
    print("Automation disconnected. Log out of Upwork in the existing Chrome window if needed.")
