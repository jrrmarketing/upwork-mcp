"""Proposal tools for Upwork MCP.

Every consequential action in this module is approval-gated before the browser is
created.  Calling an action without approval is therefore a safe prepare step: it
returns the exact payload and digest that must be approved and committed unchanged.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..browser.client import get_browser
from ..prepared_actions import authorize_action, prepare_action


class StrictToolModel(BaseModel):
    """Base model that rejects misspelled or unexpected action fields."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


def validate_upwork_url(value: str) -> str:
    """Accept only HTTPS URLs hosted by Upwork."""

    candidate = value.strip()
    parsed = urlparse(candidate)
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not hostname
        or not (hostname == "upwork.com" or hostname.endswith(".upwork.com"))
        or parsed.username
        or parsed.password
    ):
        raise ValueError("A full HTTPS Upwork URL is required")
    return candidate


def validate_job_or_invitation_url(value: str) -> str:
    candidate = validate_upwork_url(value)
    path = urlparse(candidate).path
    if not (
        path.startswith("/jobs/")
        or "/proposals/job/" in path
        or "/proposals/interview/" in path
    ):
        raise ValueError("URL must point to an Upwork job or invitation route")
    return candidate


_SUBMITTED_PROPOSAL_PATH = re.compile(r"^/nx/proposals/(?P<proposal_id>[0-9]{19})/?$")


def parse_submitted_proposal_url(value: str) -> tuple[str, str]:
    """Return the canonical individual submitted-proposal URL and its ID.

    Proposal indexes, application forms, invitations, and a matching route hidden
    in the query string are intentionally rejected.
    """

    candidate = validate_upwork_url(value)
    match = _SUBMITTED_PROPOSAL_PATH.fullmatch(urlparse(candidate).path)
    if not match:
        raise ValueError(
            "URL must point to one individual submitted proposal at /nx/proposals/<proposal_id>"
        )
    proposal_id = match.group("proposal_id")
    return f"https://www.upwork.com/nx/proposals/{proposal_id}", proposal_id


def validate_proposal_url(value: str) -> str:
    return parse_submitted_proposal_url(value)[0]


def approval_payload_digest(payload: Mapping[str, Any]) -> str:
    """Return the stable SHA-256 used to lock exact approved action payloads."""

    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def approval_gate(
    action: str,
    payload: Mapping[str, Any],
    *,
    approved: bool,
    approval_sha256: str | None,
    action_id: str | None = None,
) -> dict[str, Any] | None:
    """Return a safe prepare/error response, or ``None`` when commit is authorised."""

    expected = approval_payload_digest(payload)
    prepared = {
        "status": "approval_required",
        "action": action,
        "exact_payload": dict(payload),
        "approval_sha256": expected,
        "action_id": action_id,
        "external_action_taken": False,
    }
    if action_id:
        action_types = {
            "submit_proposal": "proposal",
            "send_message": "message",
            "withdraw_proposal": "withdrawal",
            "decline_invitation": "invitation_decline",
        }
        try:
            authorization = authorize_action(action_id, action_types.get(action, action), payload)
        except ValueError as error:
            prepared["status"] = "approval_required"
            prepared["message"] = str(error)
            return prepared
        prepared["prepared_action_authorization"] = authorization
        return None
    if not approved:
        prepared["message"] = (
            "No browser was opened. Show this exact payload to the owner and retry only "
            "after approval with approved=true and the matching approval_sha256."
        )
        return prepared
    if not approval_sha256 or not hmac.compare_digest(approval_sha256.lower(), expected):
        prepared["status"] = "approval_mismatch"
        prepared["message"] = "Approval digest is missing or does not match the exact action payload."
        return prepared
    return None


class ProposalsParams(StrictToolModel):
    """Parameters for getting proposals."""

    status: Literal["active", "submitted", "archived", "all"] = Field(
        default="active",
        description="Filter by status: active, submitted, archived, or all"
    )
    limit: int = Field(default=20, ge=1, le=50, description="Maximum number of results")


class InspectProposalFormParams(StrictToolModel):
    """Parameters for opening and reading an Upwork application form."""

    job_url: str = Field(description="Full Upwork job or invitation URL")

    _validate_job_url = field_validator("job_url")(validate_job_or_invitation_url)


class SubmitProposalParams(StrictToolModel):
    """Parameters for submitting a proposal."""

    job_url: str = Field(description="Full Upwork job URL")
    cover_letter: str = Field(min_length=1, max_length=10000, description="Exact approved cover letter")
    rate: float | None = Field(default=None, gt=0, description="Proposed hourly rate")
    bid: float | None = Field(default=None, gt=0, description="Fixed-price bid")
    answers: list[str] | None = Field(default=None, max_length=20, description="Exact screening answers")
    screening_questions: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Exact live screening-question text observed before approval",
    )
    duration: Literal[
        "Less than 1 month",
        "1 to 3 months",
        "3 to 6 months",
        "More than 6 months",
    ] | None = Field(default=None, description="Exact Upwork duration selection")
    profile_highlights: list[str] = Field(default_factory=list, max_length=4)
    base_connects: int | None = Field(
        default=None,
        ge=0,
        description="Base Connects observed in the live form before approval",
    )
    boost_connects: int = Field(default=0, ge=0)
    rate_increase_frequency: Literal["Never"] = "Never"
    approved: bool = False
    approval_sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    action_id: str | None = Field(default=None, min_length=1, max_length=128)

    _validate_job_url = field_validator("job_url")(validate_job_or_invitation_url)

    @field_validator("cover_letter")
    @classmethod
    def _cover_letter_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Cover letter cannot be blank")
        return value

    @field_validator("answers", "screening_questions", "profile_highlights")
    @classmethod
    def _list_items_must_not_be_blank(cls, values: list[str] | None) -> list[str] | None:
        if values is not None and any(not value.strip() for value in values):
            raise ValueError("List items cannot be blank")
        return values

    @model_validator(mode="after")
    def _require_exactly_one_price(self) -> SubmitProposalParams:
        if (self.rate is None) == (self.bid is None):
            raise ValueError("Provide exactly one of rate or bid")
        return self


class WithdrawProposalParams(StrictToolModel):
    """Exact approved payload for withdrawing an existing proposal."""

    proposal_url: str = Field(description="Full Upwork proposal URL")
    proposal_id: str = Field(
        pattern=r"^[0-9]{19}$",
        description="Exact 19-digit submitted proposal ID",
    )
    job_title: str = Field(min_length=1, max_length=1000, description="Live job title bound at preparation")
    proposal_status: str = Field(
        min_length=1,
        max_length=200,
        description="Live proposal status bound at preparation",
    )
    reason: str | None = Field(default=None, max_length=1000)
    approved: bool = False
    approval_sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    action_id: str | None = Field(default=None, min_length=1, max_length=128)

    _validate_proposal_url = field_validator("proposal_url")(validate_proposal_url)

    @model_validator(mode="after")
    def _proposal_id_must_match_url(self) -> WithdrawProposalParams:
        _, route_id = parse_submitted_proposal_url(self.proposal_url)
        if route_id != self.proposal_id:
            raise ValueError("proposal_id does not match the individual proposal URL")
        return self


def proposal_submission_payload(params: SubmitProposalParams) -> dict[str, Any]:
    """Return the consequential fields covered by proposal approval."""

    payload: dict[str, Any] = {
        "job_url": params.job_url,
        "cover_letter": params.cover_letter,
        "rate": params.rate,
        "bid": params.bid,
        "answers": params.answers or [],
        "screening_questions": params.screening_questions,
        "duration": params.duration,
        "profile_highlights": params.profile_highlights,
        "boost_connects": params.boost_connects,
        "rate_increase_frequency": params.rate_increase_frequency,
    }
    # Keep compatibility with proposal artifacts prepared before live-form
    # inspection was added. New preparation flows should always include it.
    if params.base_connects is not None:
        payload["base_connects"] = params.base_connects
    return payload


def proposal_withdrawal_payload(params: WithdrawProposalParams) -> dict[str, Any]:
    return {
        "proposal_url": params.proposal_url,
        "proposal_id": params.proposal_id,
        "job_title": params.job_title,
        "proposal_status": params.proposal_status,
        "reason": params.reason,
    }


async def get_proposals(params: ProposalsParams) -> list[dict]:
    """Get your submitted proposals on Upwork.

    Returns a list of proposals with job title, status, bid amount, and dates.
    """
    browser = get_browser()
    await browser.ensure_logged_in()
    async with browser.operation() as page:
        return await _get_proposals_on_page(params, page)


async def _get_proposals_on_page(params: ProposalsParams, page) -> list[dict]:
    """Read proposal rows while the browser operation lock is held."""

    # Navigate to proposals page
    status_path = {
        "active": "active",
        "submitted": "submitted",
        "archived": "archived",
        "all": ""
    }.get(params.status.lower(), "active")

    url = f"https://www.upwork.com/nx/proposals/{'?status=' + status_path if status_path else ''}"
    await page.goto(url, wait_until="networkidle")

    proposals = []

    # Wait for proposals to load
    try:
        await page.wait_for_selector('[data-test="proposal-tile"], .proposal-row', timeout=10000)
    except Exception:
        # No proposals or different structure
        pass

    # Extract proposal cards
    proposal_els = await page.query_selector_all('[data-test="proposal-tile"], .proposal-row, article')

    for el in proposal_els[:params.limit]:
        try:
            proposal = await _extract_proposal(el)
            if proposal:
                proposals.append(proposal)
        except Exception:
            continue

    return proposals


async def _extract_proposal(el) -> dict | None:
    """Extract proposal data from element."""
    proposal = {}

    # Job title
    title_el = await el.query_selector('[data-test="job-title"], .job-title, a h3, h4')
    if title_el:
        proposal["job_title"] = (await title_el.text_content() or "").strip()
        href = await title_el.get_attribute("href")
        if href:
            proposal["job_url"] = href if href.startswith("http") else f"https://www.upwork.com{href}"

    if not proposal.get("job_title"):
        return None

    # Status
    status_el = await el.query_selector('[data-test="proposal-status"], .status-badge, .proposal-status')
    if status_el:
        proposal["status"] = (await status_el.text_content() or "").strip()

    # Bid/rate
    bid_el = await el.query_selector('[data-test="bid-amount"], .bid, .rate')
    if bid_el:
        proposal["bid"] = (await bid_el.text_content() or "").strip()

    # Submitted date
    date_el = await el.query_selector('[data-test="submitted-date"], .date, time')
    if date_el:
        proposal["submitted"] = (await date_el.text_content() or "").strip()

    # Client viewed
    viewed_el = await el.query_selector('[data-test="client-viewed"], .viewed')
    proposal["client_viewed"] = viewed_el is not None

    # Interview status
    interview_el = await el.query_selector('[data-test="interview-status"], .interview')
    if interview_el:
        proposal["interview_status"] = (await interview_el.text_content() or "").strip()

    # Connects used
    connects_el = await el.query_selector('[data-test="connects-used"], .connects')
    if connects_el:
        text = (await connects_el.text_content() or "").strip()
        import re
        numbers = re.findall(r'\d+', text)
        if numbers:
            proposal["connects_used"] = int(numbers[0])

    return proposal


async def get_proposal_details(proposal_url: str) -> dict:
    """Get detailed information about a specific proposal.

    Args:
        proposal_url: URL to the proposal

    Returns details including cover letter, bid, and any messages.
    """
    proposal_url = validate_proposal_url(proposal_url)
    browser = get_browser()
    await browser.ensure_logged_in()
    async with browser.operation() as page:
        return await _get_proposal_details_on_page(proposal_url, page)


async def _get_proposal_details_on_page(proposal_url: str, page) -> dict:
    """Read one proposal while the browser operation lock is held."""

    proposal_url, proposal_id = parse_submitted_proposal_url(proposal_url)
    await page.goto(proposal_url, wait_until="networkidle")

    try:
        live_url, live_proposal_id = parse_submitted_proposal_url(str(getattr(page, "url", "")))
    except ValueError as error:
        raise ValueError("Upwork did not remain on an individual submitted-proposal route") from error
    if live_proposal_id != proposal_id:
        raise ValueError("Upwork opened a different submitted proposal than requested")

    details: dict[str, Any] = {
        "url": live_url,
        "proposal_id": proposal_id,
    }

    # Job title
    title_el = await page.query_selector('[data-test="job-title"], h1, .job-title')
    if title_el:
        details["job_title"] = (await title_el.text_content() or "").strip()

    # Cover letter
    cover_el = await page.query_selector('[data-test="cover-letter"], .cover-letter')
    if cover_el:
        details["cover_letter"] = (await cover_el.text_content() or "").strip()

    # Bid/Rate
    bid_el = await page.query_selector('[data-test="bid-amount"], .bid-amount')
    if bid_el:
        details["bid"] = (await bid_el.text_content() or "").strip()

    # Status
    status_el = await page.query_selector(
        '[data-test="proposal-status"], .proposal-status, [data-test*="proposal-status"]'
    )
    if status_el:
        details["status"] = (await status_el.text_content() or "").strip()
    if not details.get("status"):
        page_text = await _page_text(page)
        if re.search(r"proposal (?:was |has been )?withdrawn", page_text, re.I):
            details["status"] = "withdrawn"
        elif await page.query_selector('[data-test="withdraw-button"], button:has-text("Withdraw")'):
            details["status"] = "withdrawable"

    # Client response/messages
    messages = []
    message_els = await page.query_selector_all('[data-test="message"], .message-item')
    for el in message_els:
        msg_text = await el.text_content()
        if msg_text:
            messages.append(msg_text.strip())
    details["messages"] = messages

    return details


def _proposal_identity(details: Mapping[str, Any]) -> dict[str, str] | None:
    """Return the live identity/status fields that must survive owner approval."""

    proposal_id = str(details.get("proposal_id") or "").strip()
    job_title = re.sub(r"\s+", " ", str(details.get("job_title") or "")).strip()
    proposal_status = re.sub(r"\s+", " ", str(details.get("status") or "")).strip()
    if not proposal_id or not job_title or not proposal_status:
        return None
    return {
        "proposal_id": proposal_id,
        "job_title": job_title,
        "proposal_status": proposal_status,
    }


async def prepare_proposal_withdrawal(
    proposal_url: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """Read and bind one submitted proposal before creating approval state."""

    canonical_url, _ = parse_submitted_proposal_url(proposal_url)
    current = await get_proposal_details(canonical_url)
    identity = _proposal_identity(current)
    errors: list[str] = []
    if identity is None:
        errors.append("The proposal identity and live status could not be read back from Upwork")
    elif identity["proposal_status"].casefold() == "withdrawn":
        errors.append("The proposal is already withdrawn")

    payload: dict[str, Any] | None = None
    prepared = None
    if identity is not None:
        params = WithdrawProposalParams(
            proposal_url=canonical_url,
            proposal_id=identity["proposal_id"],
            job_title=identity["job_title"],
            proposal_status=identity["proposal_status"],
            reason=reason,
        )
        payload = proposal_withdrawal_payload(params)
        if not errors:
            prepared = prepare_action("withdrawal", payload)

    return {
        "ready_for_owner_approval": not errors,
        "errors": errors,
        "warning": "Withdrawing does not refund Connects or improve freelancer search visibility.",
        "current_proposal": current,
        "exact_withdrawal": payload,
        "prepared_action": prepared,
        "external_action_taken": False,
    }


async def _page_text(page) -> str:
    body = await page.query_selector("body")
    return ((await body.text_content()) if body else "") or ""


async def _click(page, element) -> None:
    """Click through Upwork overlays, falling back to a DOM click."""

    try:
        await element.click()
    except Exception:
        await page.evaluate("element => element.click()", element)


def _dedupe_text(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = re.sub(r"\s+", " ", value).strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result


def _extract_base_connects(text: str) -> int | None:
    """Extract base proposal cost without confusing it with account balance."""

    if re.search(r"no connects? (?:are )?required|costs? 0 connects?|send for 0 connects?", text, re.I):
        return 0

    relevant_lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in text.splitlines()
        if "connect" in line.lower()
        and any(term in line.lower() for term in ("required", "submit", "cost", "send for"))
    ]
    patterns = (
        r"send\s+for\s+(\d+)\s+connects?",
        r"(?:requires?|required|costs?)\D{0,30}(\d+)\s+connects?",
        r"(\d+)\s+connects?\D{0,30}(?:required|to submit|proposal cost)",
    )
    for line in relevant_lines:
        for pattern in patterns:
            match = re.search(pattern, line, re.I)
            if match:
                return int(match.group(1))
    return None


def _existing_proposal_evidence(text: str) -> str | None:
    for pattern in (
        r"you (?:have )?already submitted a proposal[^\n]*",
        r"proposal (?:has been )?submitted[^\n]*",
        r"view (?:my )?proposal[^\n]*",
    ):
        match = re.search(pattern, text, re.I)
        if match:
            return re.sub(r"\s+", " ", match.group(0)).strip()
    return None


async def _open_proposal_form(page, job_url: str) -> tuple[str, str | None]:
    """Navigate to a job and open its apply form without committing anything."""

    await page.goto(job_url, wait_until="networkidle")
    text = await _page_text(page)
    existing = _existing_proposal_evidence(text)
    if existing:
        return "already_applied", existing

    if "/proposals/" in str(getattr(page, "url", "")):
        return "ready", None
    if await page.query_selector('[data-test="cover-letter-input"], textarea[name*="cover"]'):
        return "ready", None

    apply_btn = await page.query_selector(
        '[data-test="apply-button"], button:has-text("Apply Now"), '
        'button:has-text("Accept Interview"), a:has-text("Apply Now")'
    )
    if not apply_btn:
        return "unavailable", None
    await _click(page, apply_btn)
    try:
        await page.wait_for_load_state("networkidle")
    except Exception:
        pass

    text = await _page_text(page)
    existing = _existing_proposal_evidence(text)
    if existing:
        return "already_applied", existing
    if (
        "/proposals/" in str(getattr(page, "url", ""))
        or await page.query_selector('[data-test="cover-letter-input"], textarea')
    ):
        return "ready", None
    return "unavailable", None


async def _inspect_duration_options(page, text: str) -> list[str]:
    allowed = [
        "Less than 1 month",
        "1 to 3 months",
        "3 to 6 months",
        "More than 6 months",
    ]
    found = [option for option in allowed if option in text]
    if len(found) > 1:
        return found

    toggle = await page.query_selector(
        'button:has-text("Select a duration"), '
        '.air3-dropdown-toggle:has-text("duration"), '
        '[data-test*="duration"] .air3-dropdown-toggle'
    )
    if toggle:
        try:
            await _click(page, toggle)
            text = await _page_text(page)
        except Exception:
            pass
    return [option for option in allowed if option in text]


async def _screening_question_texts(page) -> list[str]:
    values: list[str] = []
    question_els = await page.query_selector_all(
        '[data-test*="screening-question"], .screening-question, '
        '.question-answer label, label[for*="question"]'
    )
    for element in question_els:
        value = await element.text_content()
        if value:
            values.append(value)
    return _dedupe_text(values)


async def inspect_proposal_form(
    job_url: str | InspectProposalFormParams,
) -> dict[str, Any]:
    """Open and inspect an application form without filling or submitting it."""

    params = job_url if isinstance(job_url, InspectProposalFormParams) else InspectProposalFormParams(job_url=job_url)
    browser = get_browser()
    await browser.ensure_logged_in()
    async with browser.operation() as page:
        return await _inspect_proposal_form_on_page(params, page)


async def _inspect_proposal_form_on_page(params: InspectProposalFormParams, page) -> dict[str, Any]:
    """Read an already-leased page for proposal-form facts."""

    form_status, existing_evidence = await _open_proposal_form(page, params.job_url)
    text = await _page_text(page)
    normalized = re.sub(r"\s+", " ", text)

    title_el = await page.query_selector('[data-test="job-title"], h1, .job-title')
    title = (await title_el.text_content() or "").strip() if title_el else None

    question_texts = await _screening_question_texts(page)

    if re.search(r"hourly rate|hourly contract|/hr\b", normalized, re.I):
        job_type: str | None = "hourly"
    elif re.search(r"fixed[- ]price|by project|project budget", normalized, re.I):
        job_type = "fixed"
    else:
        job_type = None

    fee_net_lines = _dedupe_text(
        [
            line
            for line in text.splitlines()
            if any(
                marker in line.lower()
                for marker in ("service fee", "upwork fee", "you'll receive", "you’ll receive", "net")
            )
        ]
    )
    boost_lines = _dedupe_text(
        [
            line
            for line in text.splitlines()
            if "boost" in line.lower()
            or ("connect" in line.lower() and any(word in line.lower() for word in ("bid", "auction")))
        ]
    )
    duration_options = await _inspect_duration_options(page, text) if form_status == "ready" else []

    return {
        "job_url": params.job_url,
        "form_url": str(getattr(page, "url", params.job_url)),
        "job_title": title,
        "form_status": form_status,
        "existing_proposal": existing_evidence is not None,
        "existing_proposal_evidence": existing_evidence,
        "screening_questions": question_texts,
        "job_type": job_type,
        "base_connects": _extract_base_connects(text),
        "fee_net_text": fee_net_lines,
        "duration_options": duration_options,
        "boost_auction_text": boost_lines,
        "external_action_taken": False,
    }


async def _first_enabled(page, selector: str):
    for element in await page.query_selector_all(selector):
        try:
            if await element.is_enabled():
                return element
        except Exception:
            return element
    return None


async def _select_duration(page, duration: str) -> bool:
    toggle = await page.query_selector(
        'button:has-text("Select a duration"), '
        '.air3-dropdown-toggle:has-text("duration"), '
        '[data-test*="duration"] .air3-dropdown-toggle'
    )
    if not toggle:
        return False
    await _click(page, toggle)
    for option in await page.query_selector_all('li.air3-menu-item, [role="option"], [role="menuitem"]'):
        if re.sub(r"\s+", " ", (await option.text_content() or "")).strip() == duration:
            await _click(page, option)
            return True
    return False


async def _select_rate_increase_never(page) -> bool:
    select = await page.query_selector('select[name*="increase"], [data-test*="rate-increase"] select')
    if select:
        try:
            await select.select_option(label="Never")
            return True
        except Exception:
            return False

    toggle = await page.query_selector(
        '[data-test*="rate-increase"] button, '
        '.air3-dropdown-toggle:has-text("rate increase"), '
        'button:has-text("Select a frequency")'
    )
    if not toggle:
        # Not every form offers scheduled increases.
        return True
    await _click(page, toggle)
    for option in await page.query_selector_all('li.air3-menu-item, [role="option"], [role="menuitem"]'):
        if re.sub(r"\s+", " ", (await option.text_content() or "")).strip().lower() == "never":
            await _click(page, option)
            return True
    return False


async def _select_profile_highlights(page, highlights: list[str]) -> tuple[bool, str | None]:
    if not highlights:
        return True, None
    open_button = await page.query_selector(
        'button:has-text("Add profile highlights"), '
        'button:has-text("Add a portfolio project"), '
        '[data-test*="profile-highlight"]'
    )
    if not open_button:
        return False, "Profile highlights control not found"
    await _click(page, open_button)

    script = r"""fragment => {
      let best = null;
      for (const element of document.querySelectorAll('*')) {
        const text = element.innerText || '';
        if (text.includes(fragment) && (!best || text.length < best.text.length)) {
          best = {element, text};
        }
      }
      if (!best) return 'not-found';
      let card = best.element;
      for (let index = 0; index < 12 && card; index += 1) {
        const button = [...card.querySelectorAll('button')]
          .find(item => /Select highlight/i.test(item.innerText || ''));
        if (button) { button.click(); return 'selected'; }
        card = card.parentElement;
      }
      return 'button-not-found';
    }"""
    for highlight in highlights:
        if await page.evaluate(script, highlight) != "selected":
            return False, f"Could not select approved profile highlight: {highlight}"

    add_button = await page.query_selector('button:has-text("Add to highlights")')
    if not add_button:
        return False, "Add to highlights button not found"
    await _click(page, add_button)
    return True, None


async def _proposal_confirmation(page, timeout_seconds: float = 15) -> dict[str, Any]:
    for _ in range(max(1, int(timeout_seconds * 2))):
        url = str(getattr(page, "url", ""))
        text = await _page_text(page)
        success_text = re.search(
            r"your proposal was submitted|proposal submitted successfully|proposal has been submitted",
            text,
            re.I,
        )
        if ("/proposals/" in url and "success" in url.lower()) or success_text:
            return {
                "confirmed": True,
                "url": url,
                "evidence": success_text.group(0) if success_text else "success URL",
            }
        await asyncio.sleep(0.5)
    return {"confirmed": False, "url": str(getattr(page, "url", "")), "evidence": None}


async def submit_proposal(params: SubmitProposalParams) -> dict:
    """Submit a proposal to an Upwork job.

    IMPORTANT: This is a sensitive action that will spend Connects.
    Make sure the cover letter and rate/bid are correct before submitting.

    Returns submission status and connects used.
    """
    payload = proposal_submission_payload(params)
    blocked = approval_gate(
        "submit_proposal",
        payload,
        approved=params.approved,
        approval_sha256=params.approval_sha256,
        action_id=params.action_id,
    )
    if blocked:
        return blocked
    if params.base_connects is None:
        return {
            "status": "preflight_required",
            "message": "Inspect the live proposal form and approve its base Connects before submission.",
            "external_action_taken": False,
        }
    if params.duration is None:
        return {
            "status": "preflight_required",
            "message": "An exact project duration must be approved before submission.",
            "external_action_taken": False,
        }

    browser = get_browser()
    await browser.ensure_logged_in()
    async with browser.operation() as page:
        return await _submit_proposal_on_page(params, page)


async def _submit_proposal_on_page(params: SubmitProposalParams, page) -> dict[str, Any]:
    """Fill and commit a proposal while the browser operation lock is held."""
    assert params.duration is not None

    form_status, existing_evidence = await _open_proposal_form(page, params.job_url)
    if form_status == "already_applied":
        return {
            "status": "already_submitted",
            "message": existing_evidence,
            "external_action_taken": False,
        }
    if form_status != "ready":
        return {
            "status": "error",
            "message": "Apply form not found. Job may be closed or unavailable.",
            "external_action_taken": False,
        }

    live_text = await _page_text(page)
    live_base_connects = _extract_base_connects(live_text)
    invited = bool(re.search(r"invitation to apply|you have been invited", live_text, re.I))
    if live_base_connects is None and invited and params.base_connects == 0:
        live_base_connects = 0
    if live_base_connects is None or live_base_connects != params.base_connects:
        return {
            "status": "live_form_mismatch",
            "message": "Live base Connects could not be confirmed or changed after approval.",
            "approved_base_connects": params.base_connects,
            "live_base_connects": live_base_connects,
            "external_action_taken": False,
        }
    live_questions = await _screening_question_texts(page)
    if live_questions != params.screening_questions:
        return {
            "status": "live_form_mismatch",
            "message": "Live screening questions changed after approval.",
            "approved_screening_questions": params.screening_questions,
            "live_screening_questions": live_questions,
            "external_action_taken": False,
        }

    # Fill in rate/bid
    if params.rate is not None:
        rate_input = await _first_enabled(page, '[data-test="hourly-rate-input"], input[name*="rate"]')
        if not rate_input:
            return {"status": "error", "message": "Hourly rate input not found", "external_action_taken": False}
        await rate_input.fill(str(params.rate))

    if params.bid is not None:
        bid_input = await _first_enabled(
            page,
            '[data-test="bid-input"], input[name*="bid"], input[name*="amount"], input[placeholder="$0.00"]',
        )
        if not bid_input:
            return {"status": "error", "message": "Fixed-price bid input not found", "external_action_taken": False}
        await bid_input.fill(str(params.bid))

    # Fill cover letter
    cover_textarea = await page.query_selector('[data-test="cover-letter-input"], textarea[name*="cover"], textarea')
    if not cover_textarea:
        return {"status": "error", "message": "Cover letter input not found", "external_action_taken": False}
    await cover_textarea.fill(params.cover_letter)

    # Answer screening questions only when the live field count still matches.
    answers = params.answers or []
    question_inputs = await page.query_selector_all(
        '[data-test="question-input"], .question-answer textarea, .screening-question textarea'
    )
    if len(question_inputs) != len(answers):
        all_textareas = await page.query_selector_all("textarea")
        question_inputs = all_textareas[1:]
    if len(question_inputs) != len(answers):
        return {
            "status": "live_form_mismatch",
            "message": "The number of live screening answer fields differs from the approved answers.",
            "approved_answers": len(answers),
            "live_answer_fields": len(question_inputs),
            "external_action_taken": False,
        }
    if answers:
        for i, answer in enumerate(answers):
            if not answer.strip():
                return {
                    "status": "live_form_mismatch",
                    "message": "An approved screening answer is blank.",
                    "external_action_taken": False,
                }
            await question_inputs[i].fill(answer)

    if not await _select_duration(page, params.duration):
        return {"status": "error", "message": "Approved duration option could not be selected", "external_action_taken": False}
    if not await _select_rate_increase_never(page):
        return {"status": "error", "message": 'Rate increase frequency could not be set to "Never"', "external_action_taken": False}
    highlights_ok, highlights_error = await _select_profile_highlights(page, params.profile_highlights)
    if not highlights_ok:
        return {"status": "error", "message": highlights_error, "external_action_taken": False}

    # Submit the proposal
    submit_btn = await page.query_selector(
        '[data-test="submit-proposal"], button[type="submit"]:has-text("Submit proposal"), '
        'button:has-text("Submit proposal")'
    )
    if not submit_btn:
        return {"status": "error", "message": "Submit proposal button not found", "external_action_taken": False}

    await _click(page, submit_btn)
    consequential_click_taken = True

    immediate_confirmation = await _proposal_confirmation(page, timeout_seconds=2)
    if immediate_confirmation["confirmed"]:
        return {
            "status": "submitted",
            "connects_used": params.base_connects + params.boost_connects,
            "message": "Proposal submitted and read back from Upwork",
            "owner_system_readback": immediate_confirmation,
            "external_action_taken": True,
        }

    if params.boost_connects:
        boost_input = await _first_enabled(
            page,
            'input[name*="boost"], input[data-test*="boost"], [data-test*="boost"] input[type="number"]',
        )
        if not boost_input:
            return {
                "status": "unknown",
                "message": "Approved boost control was not found after the first submission step.",
                "external_action_taken": consequential_click_taken,
            }
        await boost_input.fill(str(params.boost_connects))
    else:
        no_boost = await page.query_selector(
            'label:has-text("Don\'t boost"), button:has-text("Don\'t boost"), '
            'label:has-text("No, thanks"), button:has-text("No, thanks")'
        )
        if no_boost:
            await _click(page, no_boost)

    send_btn = await page.query_selector(
        '[data-test="send-proposal"], button:has-text("Send for"), button:has-text("Send proposal")'
    )
    if send_btn:
        await _click(page, send_btn)

    acknowledgement = await page.query_selector(
        'input[type="checkbox"] + label:has-text("Yes, I understand"), '
        'label:has-text("Yes, I understand"), input[type="checkbox"]'
    )
    if acknowledgement:
        try:
            await acknowledgement.check()
        except Exception:
            await _click(page, acknowledgement)
        continue_btn = await page.query_selector(
            '[role="dialog"] button:has-text("Continue"), button:has-text("Continue")'
        )
        if continue_btn:
            await _click(page, continue_btn)

    readback = await _proposal_confirmation(page)
    if readback["confirmed"]:
        return {
            "status": "submitted",
            "connects_used": params.base_connects + params.boost_connects,
            "message": "Proposal submitted and read back from Upwork",
            "owner_system_readback": readback,
            "external_action_taken": True,
        }
    error_el = await page.query_selector('[data-test="error-message"], .error, .alert-danger')
    if error_el:
        error_text = (await error_el.text_content() or "").strip()
        return {
            "status": "error",
            "message": error_text,
            "owner_system_readback": readback,
            "external_action_taken": consequential_click_taken,
        }
    return {
        "status": "unknown",
        "message": "Upwork did not provide owner-system confirmation; do not retry automatically.",
        "owner_system_readback": readback,
        "external_action_taken": consequential_click_taken,
    }


async def withdraw_proposal(params: WithdrawProposalParams | str) -> dict:
    """Withdraw a submitted proposal.

    Args:
        params: Exact approved withdrawal. A legacy URL alone is not enough to
            bind the proposal identity and status, so it never opens the browser.

    Returns withdrawal status.
    """
    if isinstance(params, str):
        canonical_url, proposal_id = parse_submitted_proposal_url(params)
        return {
            "status": "preflight_required",
            "message": (
                "Read the individual proposal identity and status with the withdrawal "
                "preparation workflow before requesting owner approval."
            ),
            "proposal_url": canonical_url,
            "proposal_id": proposal_id,
            "external_action_taken": False,
        }
    payload = proposal_withdrawal_payload(params)
    blocked = approval_gate(
        "withdraw_proposal",
        payload,
        approved=params.approved,
        approval_sha256=params.approval_sha256,
        action_id=params.action_id,
    )
    if blocked:
        return blocked

    browser = get_browser()
    await browser.ensure_logged_in()
    async with browser.operation() as page:
        return await _withdraw_proposal_on_page(params, page)


async def _withdraw_proposal_on_page(params: WithdrawProposalParams, page) -> dict[str, Any]:
    """Withdraw while the browser operation lock is held."""

    current = await _get_proposal_details_on_page(params.proposal_url, page)
    live_identity = _proposal_identity(current)
    approved_identity = {
        "proposal_id": params.proposal_id,
        "job_title": re.sub(r"\s+", " ", params.job_title).strip(),
        "proposal_status": re.sub(r"\s+", " ", params.proposal_status).strip(),
    }
    if live_identity and live_identity["proposal_status"].casefold() == "withdrawn":
        return {
            "status": "already_withdrawn",
            "owner_system_readback": {
                "confirmed": True,
                "proposal_identity": live_identity,
                "url": current["url"],
            },
            "external_action_taken": False,
        }
    if live_identity != approved_identity:
        return {
            "status": "live_identity_mismatch",
            "message": (
                "The live proposal identity or status differs from the approved payload; "
                "nothing was withdrawn. Prepare the current proposal again before any new approval."
            ),
            "approved_proposal_identity": approved_identity,
            "live_proposal_identity": live_identity,
            "external_action_taken": False,
        }

    # Find withdraw button
    withdraw_btn = await page.query_selector('[data-test="withdraw-button"], button:has-text("Withdraw")')
    if not withdraw_btn:
        return {
            "status": "error",
            "message": "Withdraw button not found. Proposal may already be closed.",
            "external_action_taken": False,
        }

    await _click(page, withdraw_btn)

    if params.reason:
        reason_input = await page.query_selector(
            '[role="dialog"] textarea, [data-test*="withdraw-reason"] textarea, textarea[name*="reason"]'
        )
        if not reason_input:
            return {
                "status": "error",
                "message": "Approved withdrawal reason input not found; withdrawal was not confirmed.",
                "external_action_taken": False,
            }
        await reason_input.fill(params.reason)

    # Confirm withdrawal in modal
    confirm_btn = await page.query_selector('[data-test="confirm-withdraw"], button:has-text("Yes"), button:has-text("Confirm")')
    if not confirm_btn:
        return {
            "status": "error",
            "message": "Withdrawal confirmation button not found.",
            "external_action_taken": False,
        }
    await _click(page, confirm_btn)

    for _ in range(20):
        text = await _page_text(page)
        evidence = re.search(r"proposal (?:was |has been )?withdrawn|withdrawal confirmed", text, re.I)
        if evidence:
            try:
                readback_details = await _get_proposal_details_on_page(params.proposal_url, page)
            except ValueError:
                readback_details = {}
            readback_identity = _proposal_identity(readback_details)
            same_target = bool(
                readback_identity
                and readback_identity["proposal_id"] == params.proposal_id
                and readback_identity["job_title"] == approved_identity["job_title"]
                and "withdraw" in readback_identity["proposal_status"].casefold()
            )
            if not same_target:
                return {
                    "status": "unknown",
                    "message": (
                        "Upwork showed withdrawal text but the same proposal identity and withdrawn "
                        "status could not be read back; do not retry automatically."
                    ),
                    "owner_system_readback": {
                        "confirmed": False,
                        "evidence": evidence.group(0),
                        "proposal_identity": readback_identity,
                        "url": str(getattr(page, "url", params.proposal_url)),
                    },
                    "external_action_taken": True,
                }
            return {
                "status": "withdrawn",
                "message": "Proposal withdrawal read back from Upwork",
                "owner_system_readback": {
                    "confirmed": True,
                    "evidence": evidence.group(0),
                    "proposal_identity": readback_identity,
                    "url": str(getattr(page, "url", params.proposal_url)),
                },
                "external_action_taken": True,
            }
        await asyncio.sleep(0.5)
    return {
        "status": "unknown",
        "message": "Upwork did not confirm withdrawal; do not retry automatically.",
        "owner_system_readback": {
            "confirmed": False,
            "proposal_identity": approved_identity,
            "url": str(getattr(page, "url", params.proposal_url)),
        },
        "external_action_taken": True,
    }
