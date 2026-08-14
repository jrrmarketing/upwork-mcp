"""Offline unit tests for attached-browser session safety."""

import asyncio
import os
import stat
import threading
import time

import pytest

import upwork_mcp.browser.client as client


@pytest.fixture(autouse=True)
def private_browser_state(monkeypatch, tmp_path):
    monkeypatch.setattr(client, "STATE_DIR", tmp_path)
    monkeypatch.setattr(client, "PROFILE_DIR", tmp_path / "chrome-profile")
    monkeypatch.setattr(client, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(client, "BROWSER_OPERATION_LOCK", tmp_path / "browser-operation.lock")


class FakeCDPSession:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.detached = False

    async def send(self, method: str, params: dict) -> None:
        self.calls.append((method, params))

    async def detach(self) -> None:
        self.detached = True


class FakePage:
    def __init__(self, url: str):
        self.url = url
        self.context = None
        self.timeout = None
        self.viewport = None
        self.closed = False
        self.active_navigations = 0
        self.max_active_navigations = 0

    def is_closed(self) -> bool:
        return self.closed

    def set_default_timeout(self, timeout: int) -> None:
        self.timeout = timeout

    async def set_viewport_size(self, viewport: dict) -> None:
        self.viewport = viewport

    async def goto(self, url: str, **_kwargs) -> None:
        self.active_navigations += 1
        self.max_active_navigations = max(
            self.max_active_navigations,
            self.active_navigations,
        )
        await asyncio.sleep(0)
        self.url = url
        self.active_navigations -= 1

    async def close(self) -> None:
        self.closed = True

    async def title(self) -> str:
        return "Find Work"

    def locator(self, selector: str):
        return FakeLocator(selector)


class FakeLocator:
    def __init__(self, selector: str):
        self.selector = selector

    async def inner_text(self, **_kwargs) -> str:
        return "Jobs you might like"

    async def evaluate_all(self, _script: str) -> list[str]:
        return ["https://www.upwork.com/freelancers/josiahroche2"]


class FakeContext:
    def __init__(self, pages: list[FakePage]):
        self.pages = pages
        self.created_pages = 0
        self.sessions: list[FakeCDPSession] = []
        for page in pages:
            page.context = self

    async def new_page(self) -> FakePage:
        self.created_pages += 1
        page = FakePage("about:blank")
        page.context = self
        self.pages.append(page)
        return page

    async def new_cdp_session(self, _page: FakePage) -> FakeCDPSession:
        session = FakeCDPSession()
        self.sessions.append(session)
        return session


class FakeBrowser:
    def __init__(self, contexts: list[FakeContext]):
        self.contexts = contexts

    def is_connected(self) -> bool:
        return True

    async def new_context(self) -> FakeContext:
        context = FakeContext([])
        self.contexts.append(context)
        return context


class DisconnectedFakeBrowser(FakeBrowser):
    def is_connected(self) -> bool:
        return False


class FakeChromium:
    def __init__(self, browser: FakeBrowser):
        self.browser = browser
        self.calls: list[tuple[str, bool | None]] = []

    async def connect_over_cdp(
        self,
        endpoint_url: str,
        *,
        is_local: bool | None = None,
    ) -> FakeBrowser:
        self.calls.append((endpoint_url, is_local))
        return self.browser


class FutureFakeChromium(FakeChromium):
    def __init__(self, browser: FakeBrowser):
        super().__init__(browser)
        self.future_calls: list[tuple[str, bool | None, bool | None]] = []

    async def connect_over_cdp(
        self,
        endpoint_url: str,
        *,
        is_local: bool | None = None,
        no_defaults: bool | None = None,
    ) -> FakeBrowser:
        self.future_calls.append((endpoint_url, is_local, no_defaults))
        return self.browser


class FakePlaywright:
    def __init__(self, chromium):
        self.chromium = chromium
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


class FakePlaywrightStarter:
    def __init__(self, playwright: FakePlaywright):
        self.playwright = playwright

    async def start(self) -> FakePlaywright:
        return self.playwright


def install_fake_playwright(monkeypatch, chromium) -> FakePlaywright:
    playwright = FakePlaywright(chromium)
    monkeypatch.setattr(
        client,
        "async_playwright",
        lambda: FakePlaywrightStarter(playwright),
    )
    monkeypatch.setattr(client, "chrome_debug_status", lambda: "dedicated")
    return playwright


@pytest.mark.parametrize(
    ("url", "body", "hrefs", "expected"),
    [
        (
            "https://www.upwork.com/nx/find-work/best-matches",
            "Jobs you might like",
            ["https://www.upwork.com/freelancers/josiahroche2"],
            True,
        ),
        (
            "https://www.upwork.com/nx/client/dashboard",
            "Jobs you might like",
            ["https://www.upwork.com/freelancers/josiahroche2"],
            False,
        ),
        (
            "https://www.upwork.com/nx/find-work/best-matches",
            "Jobs you might like",
            ["https://www.upwork.com/freelancers/different-profile"],
            False,
        ),
    ],
)
def test_freelancer_context_fails_closed(
    url: str,
    body: str,
    hrefs: list[str],
    expected: bool,
) -> None:
    assert client._is_expected_freelancer_snapshot(url, body, hrefs) is expected


@pytest.mark.asyncio
async def test_sync_chrome_wait_does_not_nest_running_event_loop(monkeypatch, tmp_path):
    states = iter(["stopped", "stopped", "dedicated"])
    command = []
    sleeps = []

    monkeypatch.setattr(client, "PROFILE_DIR", tmp_path / "profile")
    monkeypatch.setattr(client, "STATE_DIR", tmp_path)
    monkeypatch.setattr(client, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(client, "BROWSER_OPERATION_LOCK", tmp_path / "browser.lock")
    monkeypatch.setattr(client, "find_chrome", lambda: "/fake/chrome")
    monkeypatch.setattr(
        client.subprocess,
        "Popen",
        lambda args, **_kwargs: command.extend(args),
    )
    monkeypatch.setattr(
        client,
        "chrome_debug_status",
        lambda: next(states),
    )
    monkeypatch.setattr(client.time, "sleep", sleeps.append)

    assert client.start_chrome_with_debug() is True
    assert sleeps == [0.5]
    assert "--window-size=1,1" in command


@pytest.mark.asyncio
async def test_start_prefers_newest_upwork_page_and_applies_desktop_metrics(monkeypatch):
    unrelated = FakePage("https://mail.google.com/mail/u/0/")
    older_upwork = FakePage("https://www.upwork.com/nx/find-work/best-matches")
    newest_upwork = FakePage("https://www.upwork.com/jobs/~012345")
    context = FakeContext([unrelated, older_upwork, newest_upwork])
    browser = FakeBrowser([context])
    chromium = FakeChromium(browser)
    install_fake_playwright(monkeypatch, chromium)

    upwork = client.UpworkBrowser(timeout=12_345)
    page = await upwork.start()

    assert page is newest_upwork
    assert page.timeout == 12_345
    assert context.created_pages == 0
    assert chromium.calls == [("http://127.0.0.1:9222", True)]
    assert context.sessions[0].calls == [
        ("Emulation.setDeviceMetricsOverride", client.DESKTOP_VIEWPORT)
    ]
    assert context.sessions[0].detached is True


@pytest.mark.asyncio
async def test_start_creates_owned_tab_instead_of_reusing_unrelated_page(monkeypatch):
    unrelated = FakePage("https://mail.google.com/mail/u/0/")
    context = FakeContext([unrelated])
    browser = FakeBrowser([context])
    install_fake_playwright(monkeypatch, FakeChromium(browser))

    upwork = client.UpworkBrowser()
    page = await upwork.start()

    assert page is not unrelated
    assert page.url == "about:blank"
    assert context.created_pages == 1
    assert upwork._page_is_owned is True


@pytest.mark.asyncio
async def test_start_uses_no_defaults_when_patchright_supports_it(monkeypatch):
    context = FakeContext([FakePage("https://www.upwork.com/nx/find-work/")])
    browser = FakeBrowser([context])
    chromium = FutureFakeChromium(browser)
    install_fake_playwright(monkeypatch, chromium)

    await client.UpworkBrowser().start()

    assert chromium.future_calls == [
        ("http://127.0.0.1:9222", True, True)
    ]


@pytest.mark.asyncio
async def test_async_start_runs_sync_chrome_launcher_in_worker(monkeypatch):
    context = FakeContext([])
    browser = FakeBrowser([context])
    chromium = FakeChromium(browser)
    playwright = FakePlaywright(chromium)
    launcher_calls = []
    monkeypatch.setattr(client, "chrome_debug_status", lambda: "stopped")

    def fake_launcher():
        launcher_calls.append(True)
        return True

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(client, "_start_chrome_with_debug_locked", fake_launcher)
    monkeypatch.setattr(client.asyncio, "sleep", no_wait)
    monkeypatch.setattr(
        client,
        "async_playwright",
        lambda: FakePlaywrightStarter(playwright),
    )

    await client.UpworkBrowser().start()

    assert launcher_calls == [True]


@pytest.mark.asyncio
async def test_start_cleans_stale_connection_before_reconnecting(monkeypatch):
    fresh_context = FakeContext([FakePage("https://www.upwork.com/nx/find-work/")])
    fresh_browser = FakeBrowser([fresh_context])
    fresh_playwright = install_fake_playwright(
        monkeypatch,
        FakeChromium(fresh_browser),
    )
    stale_playwright = FakePlaywright(FakeChromium(DisconnectedFakeBrowser([])))

    upwork = client.UpworkBrowser()
    upwork._playwright = stale_playwright
    upwork._browser = DisconnectedFakeBrowser([])
    upwork._started = True

    page = await upwork.start()

    assert stale_playwright.stopped is True
    assert upwork._playwright is fresh_playwright
    assert page is fresh_context.pages[0]


@pytest.mark.asyncio
async def test_navigation_helpers_serialize_shared_page(monkeypatch):
    page = FakePage("https://www.upwork.com/nx/find-work/")
    context = FakeContext([page])
    browser = FakeBrowser([context])
    install_fake_playwright(monkeypatch, FakeChromium(browser))
    upwork = client.UpworkBrowser()
    await upwork.start()

    await asyncio.gather(
        upwork.navigate("https://www.upwork.com/jobs/~01"),
        upwork.navigate("https://www.upwork.com/jobs/~02"),
    )

    assert page.max_active_navigations == 1


@pytest.mark.asyncio
async def test_close_disconnects_patchright_without_closing_owner_page(monkeypatch):
    page = FakePage("https://www.upwork.com/nx/find-work/")
    context = FakeContext([page])
    browser = FakeBrowser([context])
    playwright = install_fake_playwright(monkeypatch, FakeChromium(browser))
    upwork = client.UpworkBrowser()
    await upwork.start()

    await upwork.close()

    assert playwright.stopped is True
    assert page.closed is False
    assert upwork._browser is None
    assert upwork._context is None
    assert upwork._page is None
    assert upwork._started is False


def test_listener_must_use_exact_dedicated_profile(monkeypatch):
    monkeypatch.setattr(client, "_listener_pids", lambda: [123])
    monkeypatch.setattr(
        client,
        "_process_command",
        lambda _pid: f"Google Chrome --remote-debugging-port=9222 --user-data-dir={client.PROFILE_DIR}",
    )
    assert client.dedicated_chrome_pids() == [123]

    monkeypatch.setattr(
        client,
        "_process_command",
        lambda _pid: "Google Chrome --remote-debugging-port=9222 --user-data-dir=/tmp/other-profile",
    )
    assert client.dedicated_chrome_pids() == []


def test_file_lock_and_state_are_owner_only():
    with client.browser_operation_file_lock():
        assert client.BROWSER_OPERATION_LOCK.exists()

    assert stat.S_IMODE(os.stat(client.STATE_DIR).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(client.BROWSER_OPERATION_LOCK).st_mode) == 0o600


def test_file_lock_serializes_independent_callers():
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def first() -> None:
        with client.browser_operation_file_lock():
            first_entered.set()
            release_first.wait(timeout=2)

    def second() -> None:
        first_entered.wait(timeout=2)
        with client.browser_operation_file_lock():
            second_entered.set()

    first_thread = threading.Thread(target=first)
    second_thread = threading.Thread(target=second)
    first_thread.start()
    second_thread.start()
    assert first_entered.wait(timeout=1)
    time.sleep(0.05)
    assert not second_entered.is_set()
    release_first.set()
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)
    assert second_entered.is_set()


@pytest.mark.asyncio
async def test_disposable_login_check_never_navigates_existing_tab(monkeypatch):
    existing = FakePage("https://www.upwork.com/ab/proposals/")
    context = FakeContext([existing])
    browser = FakeBrowser([context])
    install_fake_playwright(monkeypatch, FakeChromium(browser))

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(client.asyncio, "sleep", no_wait)
    upwork = client.UpworkBrowser()

    assert await upwork.is_logged_in_disposable() is True
    assert existing.url == "https://www.upwork.com/ab/proposals/"
    assert existing.active_navigations == 0
    assert context.created_pages == 1
    assert context.pages[-1].closed is True


@pytest.mark.asyncio
async def test_browser_diagnostics_never_write_stdout(capsys):
    class ErrorPage(FakePage):
        async def goto(self, url: str, **_kwargs) -> None:
            raise RuntimeError("offline failure")

    result = await client.UpworkBrowser()._is_logged_in_on_page(ErrorPage("about:blank"))
    captured = capsys.readouterr()

    assert result is False
    assert captured.out == ""
    assert "offline failure" in captured.err


@pytest.mark.asyncio
async def test_browser_refuses_mismatched_debug_listener(monkeypatch, capsys):
    monkeypatch.setattr(client, "chrome_debug_status", lambda: "mismatched")

    with pytest.raises(RuntimeError, match="not using the dedicated Upwork profile"):
        await client.UpworkBrowser().start()

    assert capsys.readouterr().out == ""


def test_clear_saved_session_stops_browser_before_removing_profile(monkeypatch):
    client.PROFILE_DIR.mkdir(parents=True)
    (client.PROFILE_DIR / "Cookies").write_text("private", encoding="utf-8")
    calls = []
    monkeypatch.setattr(client, "chrome_debug_status", lambda: "dedicated")
    monkeypatch.setattr(client, "_stop_dedicated_chrome_locked", lambda: calls.append("stop") or True)

    assert client.clear_saved_session() is True
    assert calls == ["stop"]
    assert not client.PROFILE_DIR.exists()


def test_clear_saved_session_refuses_mismatched_listener(monkeypatch):
    client.PROFILE_DIR.mkdir(parents=True)
    monkeypatch.setattr(client, "chrome_debug_status", lambda: "mismatched")

    with pytest.raises(RuntimeError, match="Refusing logout"):
        client.clear_saved_session()

    assert client.PROFILE_DIR.exists()
