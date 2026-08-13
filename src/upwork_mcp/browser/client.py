"""Browser client for Upwork automation using Patchright with CDP."""

import asyncio
import inspect
import os
import subprocess
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from patchright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

PROFILE_DIR = Path.home() / ".upwork-mcp" / "chrome-profile"
CDP_PORT = 9222
DESKTOP_VIEWPORT = {
    "width": 1500,
    "height": 1150,
    "deviceScaleFactor": 1,
    "mobile": False,
}
EXPECTED_FREELANCER_SLUG = os.getenv(
    "UPWORK_FREELANCER_PROFILE_SLUG",
    "josiahroche2",
).strip().lower()

# Real Chrome paths by platform
CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",  # macOS
    "/usr/bin/google-chrome",  # Linux
    "/usr/bin/chromium-browser",  # Linux Chromium
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",  # Windows
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",  # Windows x86
]


def find_chrome() -> str | None:
    """Find real Chrome/Chromium browser on system."""
    for path in CHROME_PATHS:
        if os.path.exists(path):
            return path
    return None


def is_chrome_running_with_debug() -> bool:
    """Check if Chrome is running with debug port."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def start_chrome_with_debug() -> bool:
    """Start Chrome with remote debugging enabled.

    This function remains synchronous for the login CLI. Async callers must run it
    in a worker thread so waiting for Chrome never blocks or nests the active event
    loop.
    """
    chrome_path = find_chrome()
    if not chrome_path:
        return False

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    # Start Chrome with debugging port
    subprocess.Popen(
        [
            chrome_path,
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={PROFILE_DIR}",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=1,1",
            "--window-position=9999,9999",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait for Chrome to start
    for _ in range(10):
        if is_chrome_running_with_debug():
            return True
        time.sleep(0.5)

    return is_chrome_running_with_debug()


def _supports_keyword(callable_obj: Callable[..., Any], keyword: str) -> bool:
    """Return whether a callable accepts a named keyword or arbitrary keywords."""
    try:
        parameters = inspect.signature(callable_obj).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == keyword or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _is_upwork_page(page: Page) -> bool:
    """Return whether a page belongs to Upwork without navigating it."""
    try:
        hostname = (urlparse(page.url).hostname or "").lower()
    except Exception:
        return False
    return hostname == "upwork.com" or hostname.endswith(".upwork.com")


def _is_open_page(page: Page | None) -> bool:
    """Return whether a Patchright page can still be used."""
    if page is None:
        return False
    try:
        return not page.is_closed()
    except Exception:
        return False


def _is_expected_freelancer_snapshot(
    url: str,
    body_text: str,
    profile_hrefs: list[str],
) -> bool:
    """Fail closed unless the page is Josiah's freelancer find-work context."""
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if hostname != "upwork.com" and not hostname.endswith(".upwork.com"):
        return False
    if not parsed.path.startswith("/nx/find-work"):
        return False
    if "jobs you might like" not in body_text.lower():
        return False
    if not EXPECTED_FREELANCER_SLUG:
        return True
    expected_path = f"/freelancers/{EXPECTED_FREELANCER_SLUG}"
    return any(expected_path in href.lower() for href in profile_hrefs)


class UpworkBrowser:
    """Manages browser instance for Upwork automation via CDP."""

    def __init__(self, headless: bool = False, timeout: int = 30000):
        self.headless = headless  # Ignored for CDP mode
        self.timeout = timeout
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._page_is_owned = False
        self._started = False
        self._configured_page_ids: set[int] = set()
        self._lifecycle_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()

    async def start(self) -> Page:
        """Connect to Chrome via CDP."""
        async with self._lifecycle_lock:
            return await self._start_locked()

    def _browser_is_connected(self) -> bool:
        """Return whether the existing CDP connection is still usable."""
        if self._browser is None:
            return False
        try:
            return self._browser.is_connected()
        except Exception:
            return False

    async def _start_locked(self) -> Page:
        """Start or repair the connection while ``_lifecycle_lock`` is held."""
        if self._started and self._browser_is_connected():
            page = await self._select_safe_page()
            await self._configure_page(page)
            return page

        if self._playwright or self._browser or self._started:
            await self._stop_playwright()

        # Ensure Chrome is running with debug port
        if not is_chrome_running_with_debug():
            print("Starting Chrome with debug port...")
            if not await asyncio.to_thread(start_chrome_with_debug):
                raise RuntimeError(
                    f"Could not start Chrome. Please start it manually with:\n"
                    f'"{find_chrome()}" --remote-debugging-port={CDP_PORT}'
                )
            await asyncio.sleep(2)

        self._playwright = await async_playwright().start()

        try:
            connect = self._playwright.chromium.connect_over_cdp
            connect_options: dict[str, Any] = {}
            if _supports_keyword(connect, "is_local"):
                connect_options["is_local"] = True
            # Patchright 1.58 does not expose this yet. Supplying it automatically
            # when available prevents attached-browser defaults from modifying the
            # owner's daily Chrome session.
            if _supports_keyword(connect, "no_defaults"):
                connect_options["no_defaults"] = True

            self._browser = await connect(
                f"http://127.0.0.1:{CDP_PORT}",
                **connect_options,
            )
            self._started = True
            page = await self._select_safe_page()
            await self._configure_page(page)
            return page
        except Exception:
            await self._stop_playwright()
            raise

    async def _select_safe_page(self) -> Page:
        """Select an Upwork tab, or create an owned blank tab.

        Never reuse an unrelated page from the owner's attached Chrome session.
        The newest Upwork page is preferred, while a page created by this client is
        retained even before its first navigation.
        """
        current_page = self._page
        if current_page is not None and _is_open_page(current_page) and (
            self._page_is_owned or _is_upwork_page(current_page)
        ):
            return current_page

        if self._browser is None:
            raise RuntimeError("Browser is not connected")

        contexts = list(self._browser.contexts)
        for context in reversed(contexts):
            for page in reversed(context.pages):
                if _is_open_page(page) and _is_upwork_page(page):
                    self._context = context
                    self._page = page
                    self._page_is_owned = False
                    return page

        if contexts:
            self._context = contexts[0]
        else:
            # Normal Chrome CDP sessions expose their default context. Retain this
            # fallback for compatibility with other Chromium endpoints.
            self._context = await self._browser.new_context()

        self._page = await self._context.new_page()
        self._page_is_owned = True
        return self._page

    async def _configure_page(self, page: Page) -> None:
        """Apply stable timeouts and desktop metrics once per page."""
        page.set_default_timeout(self.timeout)
        page_id = id(page)
        if page_id in self._configured_page_ids:
            return

        applied = False
        session = None
        try:
            session = await page.context.new_cdp_session(page)
            await session.send("Emulation.setDeviceMetricsOverride", DESKTOP_VIEWPORT)
            applied = True
        except Exception:
            # Some non-Chromium test/fallback endpoints do not expose CDP sessions.
            # Patchright's viewport API is the safest compatible fallback.
            try:
                await page.set_viewport_size(
                    {
                        "width": DESKTOP_VIEWPORT["width"],
                        "height": DESKTOP_VIEWPORT["height"],
                    }
                )
                applied = True
            except Exception:
                pass
        finally:
            if session is not None:
                try:
                    await session.detach()
                except Exception:
                    pass

        if applied:
            self._configured_page_ids.add(page_id)

    async def get_page(self) -> Page:
        """Get or create page instance."""
        async with self._lifecycle_lock:
            return await self._start_locked()

    @asynccontextmanager
    async def operation(self) -> AsyncIterator[Page]:
        """Serialize a browser operation that uses the shared attached session.

        Callers performing several page actions should keep all of them inside this
        context so another MCP request cannot navigate the shared tab mid-operation.
        Existing helper methods use it without changing their public API.
        """
        async with self._operation_lock:
            yield await self.get_page()

    async def _stop_playwright(self) -> None:
        """Disconnect Patchright without closing the owner's Chrome process."""
        if self._playwright:
            await self._playwright.stop()
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._page_is_owned = False
        self._configured_page_ids.clear()
        self._started = False

    async def close(self):
        """Disconnect from browser (doesn't close Chrome)."""
        async with self._operation_lock:
            async with self._lifecycle_lock:
                await self._stop_playwright()

    async def is_logged_in(self) -> bool:
        """Check if user is authenticated on Upwork."""
        async with self.operation() as page:
            try:
                await page.goto("https://www.upwork.com/nx/find-work/best-matches", wait_until="domcontentloaded")

                # Wait for page to stabilize (Cloudflare or content)
                for _ in range(10):
                    await asyncio.sleep(2)
                    title = await page.title()
                    if "moment" not in title.lower():
                        break

                current_url = page.url.lower()
                title = await page.title()

                # Check for Cloudflare (still showing)
                if "moment" in title.lower():
                    print("Cloudflare challenge detected. Please solve it in the browser window.")
                    return False

                # Check for login redirect
                if "login" in current_url or "ab/account-security" in current_url:
                    return False

                body_text = await page.locator("body").inner_text(timeout=5000)
                profile_hrefs = await page.locator('a[href*="/freelancers/"]').evaluate_all(
                    "(links) => links.map((link) => link.href || '')"
                )
                return _is_expected_freelancer_snapshot(
                    page.url,
                    body_text,
                    profile_hrefs,
                )
            except Exception as e:
                print(f"Login check error: {e}")
                return False

    async def ensure_logged_in(self) -> bool:
        """Verify login status, raise error if not logged in."""
        if not await self.is_logged_in():
            raise RuntimeError(
                "Not logged in to Upwork. Run the configured owner login flow: "
                "'uv run upwork-mcp --login'."
            )
        return True

    async def navigate(
        self,
        url: str,
        wait_until: Literal["commit", "domcontentloaded", "load", "networkidle"] = "domcontentloaded",
    ) -> Page:
        """Navigate to URL and return page."""
        async with self.operation() as page:
            await page.goto(url, wait_until=wait_until)
            return page

    async def wait_for_selector(self, selector: str, timeout: int | None = None) -> Any:
        """Wait for element to appear."""
        async with self.operation() as page:
            return await page.wait_for_selector(selector, timeout=timeout or self.timeout)

    async def extract_text(self, selector: str, default: str = "") -> str:
        """Extract text content from selector."""
        async with self.operation() as page:
            try:
                element = await page.query_selector(selector)
                if element:
                    return (await element.text_content() or "").strip()
            except Exception:
                pass
            return default

    async def extract_texts(self, selector: str) -> list[str]:
        """Extract text from all matching elements."""
        async with self.operation() as page:
            elements = await page.query_selector_all(selector)
            texts = []
            for el in elements:
                text = await el.text_content()
                if text:
                    texts.append(text.strip())
            return texts

    async def extract_attribute(self, selector: str, attribute: str, default: str = "") -> str:
        """Extract attribute value from selector."""
        async with self.operation() as page:
            try:
                element = await page.query_selector(selector)
                if element:
                    return (await element.get_attribute(attribute)) or default
            except Exception:
                pass
            return default


# Global browser instance
_browser: UpworkBrowser | None = None


def get_browser(headless: bool = False, timeout: int = 30000) -> UpworkBrowser:
    """Get or create global browser instance."""
    global _browser
    if _browser is None:
        _browser = UpworkBrowser(headless=headless, timeout=timeout)
    return _browser


async def close_browser():
    """Close global browser instance."""
    global _browser
    if _browser:
        await _browser.close()
        _browser = None
