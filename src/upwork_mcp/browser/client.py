"""Browser client for Upwork automation using Patchright with CDP."""

import asyncio
import fcntl
import inspect
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from patchright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

STATE_DIR = Path(os.environ.get("UPWORK_MCP_STATE_DIR", "~/.upwork-mcp")).expanduser()
PROFILE_DIR = STATE_DIR / "chrome-profile"
LOG_DIR = STATE_DIR / "logs"
BROWSER_OPERATION_LOCK = STATE_DIR / "browser-operation.lock"
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


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


@contextmanager
def browser_operation_file_lock():
    """Serialize every process that can operate the dedicated Upwork browser."""
    _ensure_private_directory(STATE_DIR)
    descriptor = os.open(BROWSER_OPERATION_LOCK, os.O_CREAT | os.O_RDWR, 0o600)
    os.chmod(BROWSER_OPERATION_LOCK, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@asynccontextmanager
async def _async_browser_operation_file_lock() -> AsyncIterator[None]:
    _ensure_private_directory(STATE_DIR)
    descriptor = os.open(BROWSER_OPERATION_LOCK, os.O_CREAT | os.O_RDWR, 0o600)
    os.chmod(BROWSER_OPERATION_LOCK, 0o600)
    acquired = False
    try:
        while not acquired:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError:
                await asyncio.sleep(0.05)
        yield
    finally:
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _listener_pids() -> list[int]:
    result = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{CDP_PORT}", "-sTCP:LISTEN", "-t"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in {0, 1}:
        return []
    return sorted({int(value) for value in result.stdout.split() if value.isdigit()})


def _process_command(pid: int) -> str:
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def dedicated_chrome_pids() -> list[int]:
    expected_argument = f"--user-data-dir={PROFILE_DIR}"
    return [pid for pid in _listener_pids() if expected_argument in _process_command(pid)]


def chrome_debug_status() -> str:
    """Return stopped, dedicated, or mismatched for the CDP listener."""
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=2) as response:
            if response.status != 200:
                return "stopped"
    except Exception:
        return "stopped"
    return "dedicated" if dedicated_chrome_pids() else "mismatched"


def find_chrome() -> str | None:
    """Find real Chrome/Chromium browser on system."""
    for path in CHROME_PATHS:
        if os.path.exists(path):
            return path
    return None


def is_chrome_running_with_debug() -> bool:
    """Check that the debug port belongs to the dedicated Upwork profile."""
    return chrome_debug_status() == "dedicated"


def _start_chrome_with_debug_locked() -> bool:
    """Start the dedicated browser while the cross-process lock is held."""
    status = chrome_debug_status()
    if status == "dedicated":
        return True
    if status == "mismatched":
        return False

    chrome_path = find_chrome()
    if not chrome_path:
        return False

    _ensure_private_directory(STATE_DIR)
    _ensure_private_directory(PROFILE_DIR)
    _ensure_private_directory(LOG_DIR)

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

    for _ in range(10):
        if chrome_debug_status() == "dedicated":
            return True
        time.sleep(0.5)

    return chrome_debug_status() == "dedicated"


def start_chrome_with_debug() -> bool:
    """Start Chrome with remote debugging enabled.

    This function remains synchronous for the login CLI. Async callers must run it
    in a worker thread so waiting for Chrome never blocks or nests the active event
    loop.
    """
    with browser_operation_file_lock():
        return _start_chrome_with_debug_locked()


def _stop_dedicated_chrome_locked(timeout_seconds: float = 5.0) -> bool:
    """Stop only the exact dedicated-profile listener while the lock is held."""
    status = chrome_debug_status()
    if status == "mismatched":
        return False
    pids = dedicated_chrome_pids()
    if not pids:
        return status == "stopped"

    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        status = chrome_debug_status()
        if status == "stopped":
            return True
        if status == "mismatched":
            return False
        time.sleep(0.1)
    return False


def stop_dedicated_chrome() -> bool:
    with browser_operation_file_lock():
        return _stop_dedicated_chrome_locked()


def clear_saved_session() -> bool:
    """Stop the exact dedicated Chrome, then remove its profile under one lock."""
    with browser_operation_file_lock():
        if chrome_debug_status() == "mismatched":
            raise RuntimeError(
                f"Refusing logout because port {CDP_PORT} is not owned by the dedicated Upwork profile."
            )
        existed = PROFILE_DIR.exists()
        if not _stop_dedicated_chrome_locked():
            raise RuntimeError("Dedicated Upwork Chrome did not stop; the saved session was not removed.")
        if PROFILE_DIR.exists():
            shutil.rmtree(PROFILE_DIR)
        return existed


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
        async with self.operation() as page:
            return page

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
        await self._ensure_connected_locked()
        page = await self._select_safe_page()
        await self._configure_page(page)
        return page

    async def _ensure_connected_locked(self) -> None:
        """Connect without selecting or configuring any existing browser tab."""
        if self._started and self._browser_is_connected():
            return

        if self._playwright or self._browser or self._started:
            await self._stop_playwright()

        status = chrome_debug_status()
        if status == "mismatched":
            raise RuntimeError(
                f"Port {CDP_PORT} belongs to a Chrome process that is not using "
                f"the dedicated Upwork profile at {PROFILE_DIR}."
            )
        if status != "dedicated":
            print("Starting dedicated Upwork Chrome...", file=sys.stderr)
            if not await asyncio.to_thread(_start_chrome_with_debug_locked):
                raise RuntimeError(
                    "Could not start the dedicated Upwork Chrome profile. "
                    "Run scripts/start-chrome-daemon.sh and inspect its error."
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
        except Exception:
            await self._stop_playwright()
            raise

    async def _create_owned_page_locked(self) -> Page:
        """Create a new tab without selecting or modifying any existing tab."""
        await self._ensure_connected_locked()
        if self._browser is None:
            raise RuntimeError("Browser is not connected")
        contexts = list(self._browser.contexts)
        context = contexts[0] if contexts else await self._browser.new_context()
        page = await context.new_page()
        self._context = context
        self._page = page
        self._page_is_owned = True
        await self._configure_page(page)
        return page

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
            async with _async_browser_operation_file_lock():
                yield await self.get_page()

    @asynccontextmanager
    async def disposable_operation(self) -> AsyncIterator[Page]:
        """Use and close a new tab without navigating any existing Upwork tab."""
        async with self._operation_lock:
            async with _async_browser_operation_file_lock():
                async with self._lifecycle_lock:
                    page = await self._create_owned_page_locked()
                try:
                    yield page
                finally:
                    try:
                        await page.close()
                    finally:
                        self._configured_page_ids.discard(id(page))
                        if self._page is page:
                            self._page = None
                            self._page_is_owned = False

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

    async def _is_logged_in_on_page(self, page: Page) -> bool:
        try:
            await page.goto("https://www.upwork.com/nx/find-work/best-matches", wait_until="domcontentloaded")

            for _ in range(10):
                await asyncio.sleep(2)
                title = await page.title()
                if "moment" not in title.lower():
                    break

            current_url = page.url.lower()
            title = await page.title()

            if "moment" in title.lower():
                print("Cloudflare challenge detected in the dedicated Upwork browser.", file=sys.stderr)
                return False

            if "login" in current_url or "ab/account-security" in current_url:
                return False

            body_text = await page.locator("body").inner_text(timeout=5000)
            profile_hrefs = await page.locator('a[href*="/freelancers/"]').evaluate_all(
                "(links) => links.map((link) => link.href || '')"
            )
            return _is_expected_freelancer_snapshot(page.url, body_text, profile_hrefs)
        except Exception as error:
            print(f"Login check error: {error}", file=sys.stderr)
            return False

    async def is_logged_in(self) -> bool:
        """Check authentication on the process's normal serialized tab."""
        async with self.operation() as page:
            return await self._is_logged_in_on_page(page)

    async def is_logged_in_disposable(self) -> bool:
        """Check authentication without navigating an existing browser tab."""
        async with self.disposable_operation() as page:
            return await self._is_logged_in_on_page(page)

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
