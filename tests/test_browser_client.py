"""Offline unit tests for attached-browser session safety."""

import asyncio

import pytest

import upwork_mcp.browser.client as client


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
    monkeypatch.setattr(client, "is_chrome_running_with_debug", lambda: True)
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
    states = iter([False, True])
    command = []
    sleeps = []

    monkeypatch.setattr(client, "PROFILE_DIR", tmp_path / "profile")
    monkeypatch.setattr(client, "find_chrome", lambda: "/fake/chrome")
    monkeypatch.setattr(
        client.subprocess,
        "Popen",
        lambda args, **_kwargs: command.extend(args),
    )
    monkeypatch.setattr(
        client,
        "is_chrome_running_with_debug",
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
    thread_calls = []

    monkeypatch.setattr(client, "is_chrome_running_with_debug", lambda: False)

    async def fake_to_thread(function):
        thread_calls.append(function)
        return True

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(client.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(client.asyncio, "sleep", no_wait)
    monkeypatch.setattr(
        client,
        "async_playwright",
        lambda: FakePlaywrightStarter(playwright),
    )

    await client.UpworkBrowser().start()

    assert thread_calls == [client.start_chrome_with_debug]


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
