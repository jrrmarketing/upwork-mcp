"""Job search, normalized snapshots, and JRR screening tools."""

from __future__ import annotations

import asyncio
import re
import urllib.parse
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..browser.client import get_browser
from ..ledger import POLICY_VERSION, record_screening
from ..strategy import PricingContext, analyze_job


class JobSearchParams(BaseModel):
    """Parameters for job search."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=200, description="Search keywords")
    category: str | None = Field(
        default=None,
        description="Optional Upwork category token/UID. Text labels are retained for audit but may not be accepted by Upwork.",
    )
    budget_min: float | None = Field(default=None, ge=0, description="Minimum advertised budget/rate")
    budget_max: float | None = Field(default=None, ge=0, description="Maximum advertised budget/rate")
    experience_level: Literal["entry", "intermediate", "expert"] | None = Field(
        default=None,
        description="Experience level: entry, intermediate, or expert",
    )
    job_type: Literal["hourly", "fixed"] | None = Field(default=None, description="Job type: hourly or fixed")
    search_mode: Literal["best_matches", "most_recent"] = Field(
        default="best_matches", description="best_matches or most_recent"
    )
    limit: int = Field(default=20, ge=1, le=50, description="Maximum number of results")

    @field_validator("search_mode", mode="before")
    @classmethod
    def validate_search_mode(cls, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        if normalized not in {"best_matches", "most_recent"}:
            raise ValueError("search_mode must be best_matches or most_recent")
        return normalized

    @field_validator("job_type", mode="before")
    @classmethod
    def validate_job_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in {"hourly", "fixed"}:
            raise ValueError("job_type must be hourly or fixed")
        return normalized

    @field_validator("experience_level", mode="before")
    @classmethod
    def validate_experience_level(cls, value: str | None) -> str | None:
        return value.strip().lower() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_budget_range(self) -> JobSearchParams:
        if self.budget_min is not None and self.budget_max is not None and self.budget_min > self.budget_max:
            raise ValueError("budget_min cannot exceed budget_max")
        return self


class JobDetailsParams(BaseModel):
    """Parameters for getting job details."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    job_url: str = Field(min_length=2, max_length=500, description="Full Upwork job URL or job ID")

    @field_validator("job_url")
    @classmethod
    def validate_job_url(cls, value: str) -> str:
        if value.startswith("~") and re.fullmatch(r"~[A-Za-z0-9]+", value):
            return value
        if value.startswith("/jobs/"):
            return value
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme != "https" or parsed.hostname not in {"upwork.com", "www.upwork.com"}:
            raise ValueError("job_url must be an HTTPS Upwork job URL or an Upwork job ID")
        if not parsed.path.startswith("/jobs/"):
            raise ValueError("job_url must point to an Upwork /jobs/ route")
        return value


def _normalize_job_url(value: str) -> str:
    value = value.strip()
    if value.startswith("http"):
        return value
    if value.startswith("/jobs/"):
        return f"https://www.upwork.com{value}"
    if value.startswith("~"):
        return f"https://www.upwork.com/jobs/{value}"
    raise ValueError("Unsupported Upwork job identifier")


def _parse_money(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"\$?([\d,.]+)\s*([KMB])?", value, re.I)
    if not match:
        return None
    number = float(match.group(1).replace(",", ""))
    number *= {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get((match.group(2) or "").lower(), 1)
    return number


def parse_job_page_text(text: str) -> dict[str, Any]:
    """Parse stable job/client facts from the visible page text.

    Upwork changes CSS classes regularly.  Visible labels such as ``Proposals:`` and
    ``hire rate`` have proven materially more stable, so selectors retrieve the page
    while this pure parser normalizes the evidence.
    """

    normalized = re.sub(r"\r\n?", "\n", text)
    result: dict[str, Any] = {}

    rate = re.search(r"\$([\d,.]+)\s*(?:-|to)\s*\$([\d,.]+)\s*(?:/hr|Hourly)?", normalized, re.I)
    if rate:
        result.update(
            {
                "job_type": "hourly",
                "hourly_rate_min": _parse_money(rate.group(1)),
                "hourly_rate_max": _parse_money(rate.group(2)),
            }
        )
    else:
        fixed = re.search(r"(?:Fixed-price|Fixed price)[^\n$]*\$([\d,.]+)", normalized, re.I)
        if fixed:
            result.update({"job_type": "fixed", "budget_min": _parse_money(fixed.group(1)), "budget_max": _parse_money(fixed.group(1))})

    mappings: tuple[tuple[str, str], ...] = (
        ("proposal_count", r"Proposals:\s*\n?\s*([^\n]+)"),
        ("last_viewed", r"Last viewed by client:\s*\n?\s*([^\n]+)"),
        ("interviewing", r"Interviewing:\s*\n?\s*(\d+)"),
        ("invites_sent", r"Invites sent:\s*\n?\s*(\d+)"),
        ("unanswered_invites", r"Unanswered invites:\s*\n?\s*(\d+)"),
        ("hours_per_week", r"((?:Less than|More than)\s+\d+\s+hrs/week)"),
        ("duration", r"((?:Less than|More than)\s+\d+\s+months?|\d+\s+to\s+\d+\s+months?)"),
        ("experience_level", r"\b(Entry level|Intermediate|Expert)\b"),
    )
    for key, pattern in mappings:
        match = re.search(pattern, normalized, re.I)
        if match:
            value: Any = match.group(1).strip()
            if key in {"interviewing", "invites_sent", "unanswered_invites"}:
                value = int(value)
            result[key] = value

    connects = re.search(r"(?:Send a proposal for:\s*)?(\d+)\s+Connects", normalized, re.I)
    if connects:
        result["connects_required"] = int(connects.group(1))
    elif re.search(r"No connects are required", normalized, re.I):
        result["connects_required"] = 0

    bid_range = re.search(
        r"Bid range\s*-?\s*High\s*\$([\d,.]+)\s*\|\s*Avg\s*\$([\d,.]+)\s*\|\s*Low\s*\$([\d,.]+)",
        normalized,
        re.I,
    )
    if bid_range:
        result["bid_range"] = {
            "high": _parse_money(bid_range.group(1)),
            "average": _parse_money(bid_range.group(2)),
            "low": _parse_money(bid_range.group(3)),
        }

    client: dict[str, Any] = {}
    client["payment_verified"] = bool(re.search(r"Payment (?:method )?verified", normalized, re.I))
    patterns: tuple[tuple[str, str, Any], ...] = (
        ("rating", r"(\d+(?:\.\d+)?)\s+(?:of|out of)\s+\d+", float),
        ("total_reviews", r"\d+(?:\.\d+)?\s+of\s+(\d+)\s+reviews", int),
        ("jobs_posted", r"([\d,]+)\s+jobs posted", lambda value: int(value.replace(",", ""))),
        ("hire_rate", r"(\d+(?:\.\d+)?)%\s+hire rate", float),
        ("open_jobs", r"(\d+)\s+open jobs", int),
        ("total_hires", r"([\d,]+)\s+hires", lambda value: int(value.replace(",", ""))),
        ("active_hires", r"\d+\s+hires,\s*(\d+)\s+active", int),
        ("avg_hourly_rate_paid", r"\$([\d,.]+)\s*/hr avg hourly rate paid", _parse_money),
        ("hours", r"([\d,]+)\s+hours(?!\s+ago)", lambda value: int(value.replace(",", ""))),
        ("member_since", r"Member since\s+([^\n]+)", str),
    )
    for key, pattern, converter in patterns:
        match = re.search(pattern, normalized, re.I)
        if match:
            client[key] = converter(match.group(1).strip())
    spent = re.search(r"\$([\d,.]+\s*[KMB]?)\+?\s+total spent", normalized, re.I)
    if spent:
        client["total_spent"] = _parse_money(spent.group(1))
    result["client"] = client

    result["invited"] = bool(re.search(r"You have been invited|Invitation to apply", normalized, re.I))
    result["contract_to_hire"] = bool(re.search(r"Contract-to-hire", normalized, re.I))
    return result


def _passes_budget_filter(job: dict[str, Any], params: JobSearchParams) -> bool:
    if params.budget_min is None and params.budget_max is None:
        return True
    observed = [
        value
        for value in (
            job.get("budget_min"),
            job.get("budget_max"),
            job.get("hourly_rate_min"),
            job.get("hourly_rate_max"),
        )
        if isinstance(value, (int, float))
    ]
    if not observed:
        # Missing budget is uncertainty, not a silent rejection.
        return True
    high = max(observed)
    low = min(observed)
    if params.budget_min is not None and high < params.budget_min:
        return False
    if params.budget_max is not None and low > params.budget_max:
        return False
    return True


async def _apply_desktop_viewport(page) -> None:
    """Apply the viewport required by the daemon Chrome playbook."""
    try:
        context = page.context
        cdp = await context.new_cdp_session(page)
        await cdp.send(
            "Emulation.setDeviceMetricsOverride",
            {"width": 1500, "height": 1150, "deviceScaleFactor": 1, "mobile": False},
        )
    except Exception:
        # Reading still works on many layouts. The caller will report missing fields.
        return


async def search_jobs(params: JobSearchParams) -> list[dict]:
    """Search Best Matches or Most Recent and return normalized summaries."""
    browser = get_browser()
    await browser.ensure_logged_in()
    async with browser.operation() as page:
        await _apply_desktop_viewport(page)

        base_url = (
            "https://www.upwork.com/nx/find-work/most-recent"
            if params.search_mode == "most_recent"
            else "https://www.upwork.com/nx/find-work/best-matches"
        )
        query_params: dict[str, str] = {"q": params.query}
        if params.job_type:
            query_params["t"] = "0" if params.job_type == "hourly" else "1"
        if params.experience_level:
            level = {"entry": "1", "intermediate": "2", "expert": "3"}.get(params.experience_level.lower())
            if level:
                query_params["contractor_tier"] = level
        if params.category:
            query_params["category2"] = params.category

        await page.goto(f"{base_url}?{urllib.parse.urlencode(query_params)}", wait_until="domcontentloaded")
        await asyncio.sleep(2)

        jobs: list[dict[str, Any]] = []
        sections = await page.query_selector_all("main section, section")
        for section in sections[: params.limit * 3]:
            try:
                title_link = await section.query_selector('a[href*="/jobs/"]')
                if not title_link:
                    continue
                title = (await title_link.text_content() or "").strip()
                href = await title_link.get_attribute("href")
                if not title or not href:
                    continue
                card_text = (await section.text_content() or "").strip()
                job: dict[str, Any] = {
                    "title": title,
                    "url": _normalize_job_url(href),
                    "search_mode": params.search_mode,
                    "card_text": card_text[:2_000],
                }
                paragraphs = await section.query_selector_all("p")
                descriptions = [(await item.text_content() or "").strip() for item in paragraphs]
                descriptions = [item for item in descriptions if item]
                if descriptions:
                    job["description"] = max(descriptions, key=len)[:1_000]
                job.update(parse_job_page_text(card_text))
                if _passes_budget_filter(job, params):
                    jobs.append(job)
                if len(jobs) >= params.limit:
                    break
            except Exception:
                continue
    return jobs


async def get_job_details(params: JobDetailsParams) -> dict:
    """Return a complete, normalized job/client snapshot from the live posting."""
    browser = get_browser()
    await browser.ensure_logged_in()
    url = _normalize_job_url(params.job_url)
    async with browser.operation() as page:
        await _apply_desktop_viewport(page)
        await page.goto(url, wait_until="domcontentloaded")
        await asyncio.sleep(2)

        main = await page.query_selector("main")
        visible_text = (await main.inner_text() if main else await page.locator("body").inner_text()).strip()
        job: dict[str, Any] = {"url": url, "snapshot_text": visible_text[:20_000]}

        title_el = await page.query_selector("main h1, main h2, main h3, main h4")
        if title_el:
            job["title"] = (await title_el.text_content() or "").strip()

        paragraphs = await page.query_selector_all("main p")
        paragraph_texts = [(await item.text_content() or "").strip() for item in paragraphs]
        paragraph_texts = [item for item in paragraph_texts if len(item) >= 80]
        if paragraph_texts:
            job["description"] = max(paragraph_texts, key=len)

        skill_els = await page.query_selector_all('main a[href*="ontology_skill_uid"]')
        skills = [(await item.text_content() or "").strip() for item in skill_els]
        if skills:
            job["skills"] = list(dict.fromkeys(item for item in skills if item))

        job.update(parse_job_page_text(visible_text))
    return job


async def screen_job(
    job_url: str,
    *,
    profile_hourly_rate: float = 63,
    minimum_hourly_rate: float = 50,
    minimum_fixed_fee: float | None = None,
    record: bool = True,
) -> dict[str, Any]:
    """Fetch one live posting and apply the JRR decision system."""
    details = await get_job_details(JobDetailsParams(job_url=job_url))
    analysis = analyze_job(
        details,
        PricingContext(
            profile_hourly_rate=profile_hourly_rate,
            minimum_hourly_rate=minimum_hourly_rate,
            minimum_fixed_fee=minimum_fixed_fee,
        ),
    ).model_dump()
    ledger = record_screening(details, analysis, policy_version=POLICY_VERSION) if record else {"recorded": False}
    return {"job": details, "analysis": analysis, "policy_version": POLICY_VERSION, "local_ledger": ledger}
