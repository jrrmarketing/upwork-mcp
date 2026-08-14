"""Proposal tools for Upwork MCP.

Every consequential action in this module is approval-gated before the browser is
created. Proposal submission accepts only an approved one-time prepared action;
other guarded actions retain their exact-payload preparation interfaces.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any, Literal, cast
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..browser.client import get_browser
from ..prepared_actions import authorize_action, prepare_action


class StrictToolModel(BaseModel):
    """Base model that rejects misspelled or unexpected action fields."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


type DiscoveryStatus = Literal["complete", "incomplete", "unavailable"]
type RateIncreaseControlStatus = Literal[
    "complete",
    "not_applicable",
    "incomplete",
    "unavailable",
]


def validate_upwork_url(value: str) -> str:
    """Accept only HTTPS URLs hosted by Upwork."""

    candidate = value.strip()
    parsed = urlparse(candidate)
    hostname = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("A full HTTPS Upwork URL is required") from error
    if (
        parsed.scheme != "https"
        or not hostname
        or not (hostname == "upwork.com" or hostname.endswith(".upwork.com"))
        or parsed.username
        or parsed.password
        or port is not None
    ):
        raise ValueError("A full HTTPS Upwork URL is required")
    return candidate


_JOB_ID = r"~[A-Za-z0-9]{3,64}"
_JOB_PATH = re.compile(rf"^/jobs/(?P<job_id>{_JOB_ID})/?$")
_APPLICATION_PATH = re.compile(
    rf"^/(?:nx|ab)/proposals/job/(?P<job_id>{_JOB_ID})/apply/?$"
)


def parse_job_url(value: str) -> tuple[str, str]:
    """Return one canonical public-job URL and its exact Upwork job ID."""

    candidate = validate_upwork_url(value)
    match = _JOB_PATH.fullmatch(urlparse(candidate).path)
    if not match:
        raise ValueError("URL must point to one job at /jobs/~<job_id>")
    job_id = match.group("job_id")
    return f"https://www.upwork.com/jobs/{job_id}", job_id


def parse_application_url(value: str) -> tuple[str, str]:
    """Return the canonical application-form URL and its exact Upwork job ID."""

    candidate = validate_upwork_url(value)
    match = _APPLICATION_PATH.fullmatch(urlparse(candidate).path)
    if not match:
        raise ValueError(
            "URL must point to one application form at /nx/proposals/job/~<job_id>/apply"
        )
    job_id = match.group("job_id")
    return f"https://www.upwork.com/nx/proposals/job/{job_id}/apply", job_id


def parse_job_or_application_url(value: str) -> tuple[str, str, Literal["job", "application"]]:
    """Parse only an exact individual public-job or application-form route."""

    try:
        canonical, job_id = parse_job_url(value)
        return canonical, job_id, "job"
    except ValueError:
        canonical, job_id = parse_application_url(value)
        return canonical, job_id, "application"


def validate_job_or_application_url(value: str) -> str:
    """Accept and canonicalize only one exact job or application route."""
    return parse_job_or_application_url(value)[0]


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

    job_url: str = Field(description="Full individual Upwork job or application-form URL")

    _validate_job_url = field_validator("job_url")(validate_job_or_application_url)


class FixedPriceMilestone(StrictToolModel):
    """One exact owner-approved fixed-price milestone."""

    description: str = Field(min_length=1, max_length=500)
    due_date: str = Field(description="Exact ISO due date (YYYY-MM-DD)")
    amount: float = Field(gt=0)

    @field_validator("description")
    @classmethod
    def _description_must_not_be_blank(cls, value: str) -> str:
        normalized = re.sub(r"\s+", " ", value).strip()
        if not normalized:
            raise ValueError("Milestone description cannot be blank")
        return normalized

    @field_validator("due_date")
    @classmethod
    def _due_date_must_be_iso(cls, value: str) -> str:
        try:
            parsed = date.fromisoformat(value)
        except ValueError as error:
            raise ValueError("Milestone due_date must use YYYY-MM-DD") from error
        return parsed.isoformat()

    @field_validator("amount")
    @classmethod
    def _amount_must_use_cents(cls, value: float) -> float:
        decimal_value = Decimal(str(value))
        if decimal_value != decimal_value.quantize(Decimal("0.01")):
            raise ValueError("Milestone amount cannot use fractions of a cent")
        return value


def validate_payment_terms(
    *,
    rate: float | None,
    bid: float | None,
    payment_structure: Literal["by_project", "by_milestone"] | None,
    milestones: list[FixedPriceMilestone],
) -> None:
    """Validate exact hourly/fixed commercial terms before approval or commit."""

    if (rate is None) == (bid is None):
        raise ValueError("Provide exactly one of rate or bid")
    approved_price = Decimal(str(rate if rate is not None else bid))
    if not approved_price.is_finite() or approved_price != approved_price.quantize(Decimal("0.01")):
        raise ValueError("Proposal prices must be finite and cannot use fractions of a cent")
    if rate is not None:
        if payment_structure is not None or milestones:
            raise ValueError("Hourly proposals cannot include fixed-price payment terms")
        return
    if payment_structure is None:
        raise ValueError("Fixed-price proposals require an explicit payment_structure")
    if payment_structure == "by_project":
        if milestones:
            raise ValueError("By-project proposals cannot include milestones")
        return
    if not milestones:
        raise ValueError("By-milestone proposals require at least one exact milestone")
    assert bid is not None
    milestone_total = sum((Decimal(str(item.amount)) for item in milestones), Decimal("0"))
    if milestone_total != Decimal(str(bid)):
        raise ValueError("Milestone amounts must add up exactly to the fixed-price bid")


class SubmitProposalParams(StrictToolModel):
    """Parameters for submitting a proposal."""

    job_url: str = Field(description="Canonical individual Upwork job URL")
    job_id: str = Field(pattern=rf"^{_JOB_ID}$", description="Exact job ID bound during preparation")
    form_url: str = Field(description="Canonical individual Upwork application-form URL")
    job_title: str = Field(min_length=1, max_length=1000, description="Exact live job title")
    job_type: Literal["hourly", "fixed"]
    cover_letter: str = Field(min_length=1, max_length=10000, description="Exact approved cover letter")
    fee_net_text: list[str] = Field(
        description="Normalized live Upwork fee/net context shown during preparation",
    )
    fee_net_status: DiscoveryStatus = Field(
        description="Completeness of the live fee/net inspection bound during preparation",
    )
    boost_auction_text: list[str] = Field(
        description="Normalized live boost-auction context shown during preparation",
    )
    boost_auction_status: DiscoveryStatus = Field(
        description="Completeness of the live boost-auction inspection bound during preparation",
    )
    rate: float | None = Field(default=None, gt=0, description="Proposed hourly rate")
    bid: float | None = Field(default=None, gt=0, description="Fixed-price bid")
    payment_structure: Literal["by_project", "by_milestone"] | None = Field(
        default=None,
        description="Required explicit structure for fixed-price proposals",
    )
    milestones: list[FixedPriceMilestone] = Field(default_factory=list, max_length=20)
    answers: list[str] | None = Field(default=None, max_length=20, description="Exact screening answers")
    screening_questions: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Exact live screening-question text observed before approval",
    )
    screening_questions_status: DiscoveryStatus = Field(
        description="Completeness of live screening-question enumeration",
    )
    duration: Literal[
        "Less than 1 month",
        "1 to 3 months",
        "3 to 6 months",
        "More than 6 months",
    ] | None = Field(default=None, description="Exact Upwork duration selection")
    duration_options_status: DiscoveryStatus = Field(
        description="Completeness of live duration-option enumeration",
    )
    profile_highlights: list[str] = Field(default_factory=list, max_length=4)
    available_profile_highlights_status: DiscoveryStatus = Field(
        description="Completeness of live profile-highlight enumeration",
    )
    base_connects: int | None = Field(
        default=None,
        ge=0,
        description="Base Connects observed in the live form before approval",
    )
    boost_connects: int = Field(default=0, ge=0)
    rate_increase_frequency: Literal["Never"] = "Never"
    rate_increase_control_status: RateIncreaseControlStatus = Field(
        description="Whether the live form exposed a complete rate-increase control or proved it inapplicable",
    )
    action_id: str = Field(min_length=1, max_length=128, description="Approved one-time prepared action ID")

    _validate_job_url = field_validator("job_url")(lambda value: parse_job_url(value)[0])
    _validate_form_url = field_validator("form_url")(lambda value: parse_application_url(value)[0])

    @field_validator("job_title")
    @classmethod
    def _normalise_job_title(cls, value: str) -> str:
        normalized = re.sub(r"\s+", " ", value).strip()
        if not normalized:
            raise ValueError("job_title cannot be blank")
        return normalized

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

    @field_validator("fee_net_text", "boost_auction_text")
    @classmethod
    def _normalise_live_context(cls, values: list[str]) -> list[str]:
        return normalize_live_context_lines(values)

    @model_validator(mode="after")
    def _bind_identity_and_terms(self) -> SubmitProposalParams:
        _, job_route_id = parse_job_url(self.job_url)
        _, form_route_id = parse_application_url(self.form_url)
        if self.job_id != job_route_id or self.job_id != form_route_id:
            raise ValueError("job_id must match both the individual job and application-form URLs")
        if self.job_type == "hourly" and self.rate is None:
            raise ValueError("An hourly form requires an hourly rate")
        if self.job_type == "fixed" and self.bid is None:
            raise ValueError("A fixed-price form requires a fixed bid")
        validate_payment_terms(
            rate=self.rate,
            bid=self.bid,
            payment_structure=self.payment_structure,
            milestones=self.milestones,
        )
        required_complete = {
            "fee/net": self.fee_net_status,
            "screening-question": self.screening_questions_status,
            "duration-option": self.duration_options_status,
            "profile-highlight": self.available_profile_highlights_status,
        }
        incomplete = [label for label, status in required_complete.items() if status != "complete"]
        if incomplete:
            raise ValueError(
                "Approved proposal payload requires complete live inspection for: "
                + ", ".join(incomplete)
            )
        if not self.fee_net_text:
            raise ValueError("Complete fee/net inspection requires normalized live fee/net text")
        if len(self.screening_questions) != len(self.answers or []):
            raise ValueError("Screening questions and exact approved answers must have equal counts")
        if self.boost_auction_status == "complete" and not self.boost_auction_text:
            raise ValueError("Complete boost-auction inspection requires normalized live auction text")
        if self.boost_auction_status == "unavailable" and self.boost_auction_text:
            raise ValueError("Unavailable boost-auction inspection cannot include live auction text")
        if self.boost_connects and self.boost_auction_status != "complete":
            raise ValueError("A nonzero boost requires a complete live boost-auction inspection")
        if self.rate_increase_control_status not in {"complete", "not_applicable"}:
            raise ValueError(
                "Rate-increase control inspection must be complete or explicitly not_applicable"
            )
        if self.job_type == "fixed" and self.rate_increase_control_status != "not_applicable":
            raise ValueError("Fixed-price proposals require rate_increase_control_status=not_applicable")
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
        "job_id": params.job_id,
        "form_url": params.form_url,
        "job_title": params.job_title,
        "job_type": params.job_type,
        "cover_letter": params.cover_letter,
        "fee_net_text": params.fee_net_text,
        "fee_net_status": params.fee_net_status,
        "boost_auction_text": params.boost_auction_text,
        "boost_auction_status": params.boost_auction_status,
        "rate": params.rate,
        "bid": params.bid,
        "payment_structure": params.payment_structure,
        "milestones": [item.model_dump(mode="json") for item in params.milestones],
        "answers": params.answers or [],
        "screening_questions": params.screening_questions,
        "screening_questions_status": params.screening_questions_status,
        "duration": params.duration,
        "duration_options_status": params.duration_options_status,
        "profile_highlights": params.profile_highlights,
        "available_profile_highlights_status": params.available_profile_highlights_status,
        "boost_connects": params.boost_connects,
        "rate_increase_frequency": params.rate_increase_frequency,
        "rate_increase_control_status": params.rate_increase_control_status,
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
    job_link = await page.query_selector('a[href^="/jobs/~"], a[href^="https://www.upwork.com/jobs/~"]')
    if job_link:
        try:
            href = str(await job_link.get_attribute("href") or "")
            if href.startswith("/"):
                href = f"https://www.upwork.com{href}"
            live_job_url, live_job_id = parse_job_url(href)
        except (AttributeError, ValueError):
            pass
        else:
            details["job_url"] = live_job_url
            details["job_id"] = live_job_id

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
        else:
            live_status = re.search(
                r"\b(active|submitted|archived|closed)\s+proposal\b|"
                r"\bproposal\s+(?:status\s*[:\-]?\s*)?(active|submitted|archived|closed)\b",
                page_text,
                re.I,
            )
            if live_status:
                details["status"] = next(
                    group for group in live_status.groups() if group is not None
                ).lower()

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


def normalize_live_context_lines(values: list[str]) -> list[str]:
    """Normalize read-only Upwork context before it enters an approval digest."""

    return _dedupe_text([str(value) for value in values])


def _inspect_fee_net_context(text: str) -> dict[str, Any]:
    """Classify the live fee/net preview without guessing from unrelated page copy."""

    lines = normalize_live_context_lines(
        [
            line
            for line in text.splitlines()
            if re.search(
                r"service fee|upwork fee|you(?:'|’)?ll receive|you will receive|\bnet\b",
                line,
                re.I,
            )
        ]
    )
    fee_lines = [line for line in lines if re.search(r"service fee|upwork fee", line, re.I)]
    net_lines = [
        line
        for line in lines
        if re.search(r"you(?:'|’)?ll receive|you will receive|\bnet\b", line, re.I)
    ]
    if fee_lines and net_lines:
        status: DiscoveryStatus = "complete"
        message = "Both the live Upwork fee and freelancer net preview were read."
    elif lines:
        status = "incomplete"
        message = "Only part of the live fee/net preview could be read."
    else:
        status = "unavailable"
        message = "The live form did not expose a readable fee/net preview."
    return {
        "text": lines,
        "status": status,
        "details": {
            "fee_lines_seen": len(fee_lines),
            "net_lines_seen": len(net_lines),
            "message": message,
        },
    }


def _inspect_boost_auction_context(text: str) -> dict[str, Any]:
    """Classify visible boost-auction evidence for exact approval binding."""

    all_lines = normalize_live_context_lines(text.splitlines())
    page_has_boost_context = any(
        re.search(r"\bboost(?:ed|ing)?\b|\bauction\b", line, re.I)
        for line in all_lines
    )
    lines = normalize_live_context_lines(
        [
            line
            for line in all_lines
            if re.search(r"\bboost(?:ed|ing)?\b|\bauction\b", line, re.I)
            or (
                re.search(r"\bconnects?\b", line, re.I)
                and re.search(r"\bbid(?:s|ding)?\b|\bauction\b|\brank(?:ed|ing)?\b", line, re.I)
            )
            or (
                page_has_boost_context
                and re.search(
                    r"\btop\s+bid\b|\bno\s+bids?\b|\bbe\s+the\s+first\b|"
                    r"\brank(?:ed|ing)?\b|\bslot\b|\b(?:1st|2nd|3rd|4th)\s+place\b",
                    line,
                    re.I,
                )
            )
        ]
    )
    has_boost_context = any(re.search(r"\bboost(?:ed|ing)?\b|\bauction\b", line, re.I) for line in lines)
    has_auction_state = any(
        re.search(
            r"\d+\s+connects?\b|\btop\s+bid\b|\bno\s+bids?\b|\bbe\s+the\s+first\b|"
            r"\brank(?:ed|ing)?\b|\bslot\b|\b(?:1st|2nd|3rd|4th)\s+place\b",
            line,
            re.I,
        )
        for line in lines
    )
    if has_boost_context and has_auction_state:
        status: DiscoveryStatus = "complete"
        message = "The live boost auction and its current state were read."
    elif lines:
        status = "incomplete"
        message = "Boost copy was visible, but the live auction state could not be proven complete."
    else:
        status = "unavailable"
        message = "The live form did not expose readable boost-auction context."
    return {
        "text": lines,
        "status": status,
        "details": {
            "boost_context_seen": has_boost_context,
            "auction_state_seen": has_auction_state,
            "message": message,
        },
    }


async def _inspect_fee_net_state(page, text: str | None = None) -> dict[str, Any]:
    """Return the normalized live fee/net state used by prepare and commit checks."""

    return _inspect_fee_net_context(text if text is not None else await _page_text(page))


async def _inspect_boost_auction_state(page, text: str | None = None) -> dict[str, Any]:
    """Return the normalized live boost-auction state used by prepare and commit checks."""

    return _inspect_boost_auction_context(text if text is not None else await _page_text(page))


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


def _normalise_identity_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _detect_job_type(text: str) -> Literal["hourly", "fixed"] | None:
    normalized = re.sub(r"\s+", " ", text)
    # Prefer form-structure labels over incidental job-description/profile text.
    if re.search(r"\bby project\b|\bby milestone\b", normalized, re.I):
        return "fixed"
    if re.search(r"rate increase frequency|select a frequency", normalized, re.I):
        return "hourly"
    fixed = re.search(r"fixed[- ]price|project budget", normalized, re.I)
    hourly = re.search(r"hourly rate|hourly contract|/hr\b", normalized, re.I)
    if fixed and not hourly:
        return "fixed"
    if hourly and not fixed:
        return "hourly"
    return None


async def _application_identity_from_current_page(page) -> dict[str, str] | None:
    """Read only immutable application identity, without querying form controls."""

    try:
        form_url, job_id = parse_application_url(str(getattr(page, "url", "")))
    except ValueError:
        return None
    text = await _page_text(page)
    title_el = await page.query_selector(
        f'[data-test="job-title"], .job-title, a[href="/jobs/{job_id}"], '
        f'a[href^="/jobs/{job_id}?"]'
    )
    title = _normalise_identity_text(await title_el.text_content()) if title_el else ""
    job_type = _detect_job_type(text)
    if not title or job_type is None:
        return None
    job_url, _ = parse_job_url(f"https://www.upwork.com/jobs/{job_id}")
    return {
        "job_url": job_url,
        "job_id": job_id,
        "form_url": form_url,
        "job_title": title,
        "job_type": job_type,
    }


async def _open_proposal_form(page, job_url: str) -> tuple[str, str | None]:
    """Navigate to a job and open its apply form without committing anything."""

    target_url, expected_job_id, target_kind = parse_job_or_application_url(job_url)
    await page.goto(target_url, wait_until="networkidle")
    text = await _page_text(page)
    existing = _existing_proposal_evidence(text)
    if existing:
        return "already_applied", existing

    live_url = str(getattr(page, "url", ""))
    try:
        _, live_job_id = parse_application_url(live_url)
    except ValueError:
        live_job_id = None
    if live_job_id is not None:
        if live_job_id != expected_job_id:
            return "identity_mismatch", None
        return "ready", None
    if target_kind == "application":
        return "identity_mismatch", None

    try:
        _, live_job_id = parse_job_url(live_url)
    except ValueError:
        return "identity_mismatch", None
    if live_job_id != expected_job_id:
        return "identity_mismatch", None

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
    try:
        _, live_job_id = parse_application_url(str(getattr(page, "url", "")))
    except ValueError:
        return "identity_mismatch", None
    if live_job_id == expected_job_id:
        return "ready", None
    return "identity_mismatch", None


_DURATION_OPTIONS = [
    "Less than 1 month",
    "1 to 3 months",
    "3 to 6 months",
    "More than 6 months",
]
_DURATION_TOGGLE = (
    'button:has-text("Select a duration"), '
    '.air3-dropdown-toggle:has-text("duration"), '
    '[data-test*="duration"] .air3-dropdown-toggle'
)
_DURATION_MENU_OPTIONS = (
    '[role="listbox"] [role="option"], .air3-menu [role="menuitem"], '
    '.air3-menu li.air3-menu-item, [data-test*="duration"] [role="option"]'
)


async def _visible_texts(page, selector: str) -> list[str]:
    values: list[str] = []
    for element in await page.query_selector_all(selector):
        if not await _element_is_visible(element):
            continue
        value = _normalise_identity_text(await element.text_content())
        if value:
            values.append(value)
    return _dedupe_text(values)


async def _inspect_duration_options(page) -> dict[str, Any]:
    """Open, enumerate, and dismiss the duration menu without selecting a value."""

    details: dict[str, Any] = {
        "expected_options": list(_DURATION_OPTIONS),
        "toggle_count": 0,
        "menu_opened": False,
        "menu_dismissed": True,
        "visible_options": [],
        "missing_options": list(_DURATION_OPTIONS),
        "unexpected_options": [],
        "message": "The duration menu was not inspected.",
    }
    toggles = [
        element
        for element in await page.query_selector_all(_DURATION_TOGGLE)
        if await _element_is_visible(element)
    ]
    details["toggle_count"] = len(toggles)
    if not toggles:
        details["message"] = "Exactly one visible duration control could not be identified."
        return {"options": [], "status": "unavailable", "details": details}
    if len(toggles) != 1:
        details["message"] = "Multiple visible duration controls made enumeration ambiguous."
        return {"options": [], "status": "incomplete", "details": details}

    try:
        await _click(page, toggles[0])
        await _settle_profile_highlight_view(page)
    except Exception as error:
        details["message"] = f"The duration menu could not be opened: {type(error).__name__}."
        return {"options": [], "status": "unavailable", "details": details}

    visible_options = await _visible_texts(page, _DURATION_MENU_OPTIONS)
    details["menu_opened"] = bool(visible_options)
    details["visible_options"] = visible_options
    options = [option for option in _DURATION_OPTIONS if option in visible_options]
    details["missing_options"] = [option for option in _DURATION_OPTIONS if option not in options]
    details["unexpected_options"] = [option for option in visible_options if option not in _DURATION_OPTIONS]

    try:
        await page.keyboard.press("Escape")
        await _settle_profile_highlight_view(page)
        details["menu_dismissed"] = not bool(await _visible_texts(page, _DURATION_MENU_OPTIONS))
    except Exception:
        details["menu_dismissed"] = False

    complete = bool(
        details["menu_opened"]
        and details["menu_dismissed"]
        and not details["missing_options"]
        and not details["unexpected_options"]
    )
    if complete:
        details["message"] = "All exact duration options were enumerated and the menu was dismissed."
        status = "complete"
    else:
        details["message"] = "Duration-option enumeration could not be proven complete."
        status = "incomplete"
    return {"options": options, "status": status, "details": details}


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


_SCREENING_QUESTION_PROMPTS = (
    '[data-test*="screening-question"], .screening-question, '
    '.question-answer label, label[for*="question"]'
)
_SCREENING_ANSWER_CONTROLS = (
    '[data-test="question-input"], .question-answer textarea, .screening-question textarea'
)
_COVER_LETTER_CONTROL = '[data-test="cover-letter-input"], textarea[name*="cover"]'


async def _inspect_screening_questions(page) -> dict[str, Any]:
    """Prove question enumeration against the corresponding live answer controls."""

    try:
        prompt_elements = [
            element
            for element in await page.query_selector_all(_SCREENING_QUESTION_PROMPTS)
            if await _element_is_visible(element)
        ]
        raw_prompts = [
            _normalise_identity_text(await element.text_content())
            for element in prompt_elements
        ]
        questions = _dedupe_text([prompt for prompt in raw_prompts if prompt])
        answer_controls = [
            element
            for element in await page.query_selector_all(_SCREENING_ANSWER_CONTROLS)
            if await _element_is_visible(element)
        ]
        all_textareas = [
            element
            for element in await page.query_selector_all("textarea")
            if await _element_is_visible(element)
        ]
        cover_controls = [
            element
            for element in await page.query_selector_all(_COVER_LETTER_CONTROL)
            if await _element_is_visible(element)
        ]
    except Exception as error:
        return {
            "questions": [],
            "status": "unavailable",
            "details": {
                "prompt_elements_seen": 0,
                "blank_prompt_elements_seen": 0,
                "questions_extracted": 0,
                "answer_controls_seen": 0,
                "cover_letter_controls_seen": 0,
                "total_textareas_seen": 0,
                "expected_total_textareas": None,
                "message": f"Screening-question inspection failed: {type(error).__name__}.",
            },
        }
    details: dict[str, Any] = {
        "prompt_elements_seen": len(raw_prompts),
        "blank_prompt_elements_seen": len([prompt for prompt in raw_prompts if not prompt]),
        "questions_extracted": len(questions),
        "answer_controls_seen": len(answer_controls),
        "cover_letter_controls_seen": len(cover_controls),
        "total_textareas_seen": len(all_textareas),
        "expected_total_textareas": len(answer_controls) + 1,
        "message": "Screening-question enumeration could not be proven complete.",
    }
    complete = bool(
        len(cover_controls) == 1
        and all(raw_prompts)
        and len(questions) == len(answer_controls)
        and len(all_textareas) == len(answer_controls) + 1
    )
    if complete:
        details["message"] = (
            "Every visible screening prompt matches one answer control; "
            "the remaining textarea is the cover letter."
        )
        status = "complete"
    elif not cover_controls and not raw_prompts and not answer_controls and not all_textareas:
        status = "unavailable"
        details["message"] = "The proposal form controls were unavailable for question enumeration."
    else:
        status = "incomplete"
    return {"questions": questions, "status": status, "details": details}


_RATE_INCREASE_SELECT = 'select[name*="increase"], [data-test*="rate-increase"] select'
_RATE_INCREASE_TOGGLE = (
    '[data-test*="rate-increase"] button, '
    '.air3-dropdown-toggle:has-text("rate increase"), '
    'button:has-text("Select a frequency")'
)


async def _inspect_rate_increase_control(
    page,
    job_type: Literal["hourly", "fixed"] | None,
) -> dict[str, Any]:
    """Read whether the exact live form supports choosing the required Never value."""

    details: dict[str, Any] = {
        "job_type": job_type,
        "select_controls_seen": 0,
        "toggle_controls_seen": 0,
        "visible_options": [],
        "menu_dismissed": True,
        "message": "The rate-increase control was not inspected.",
    }
    if job_type == "fixed":
        details["message"] = "Rate increases do not apply to this fixed-price form."
        return {"status": "not_applicable", "details": details}
    if job_type != "hourly":
        details["message"] = "The job type is unavailable, so rate-increase applicability is unknown."
        return {"status": "unavailable", "details": details}

    try:
        selects = [
            element
            for element in await page.query_selector_all(_RATE_INCREASE_SELECT)
            if await _element_is_visible(element)
        ]
        toggles = [
            element
            for element in await page.query_selector_all(_RATE_INCREASE_TOGGLE)
            if await _element_is_visible(element)
        ]
    except Exception as error:
        details["message"] = f"Rate-increase control inspection failed: {type(error).__name__}."
        return {"status": "unavailable", "details": details}

    details["select_controls_seen"] = len(selects)
    details["toggle_controls_seen"] = len(toggles)
    if not selects and not toggles:
        details["message"] = "The exact hourly form exposes no rate-increase control."
        return {"status": "not_applicable", "details": details}
    if len(selects) + len(toggles) != 1:
        details["message"] = "Multiple visible rate-increase controls made inspection ambiguous."
        return {"status": "incomplete", "details": details}

    if selects:
        try:
            option_elements = await selects[0].query_selector_all("option")
            options = _dedupe_text(
                [
                    _normalise_identity_text(await option.text_content())
                    for option in option_elements
                ]
            )
        except Exception as error:
            details["message"] = f"Rate-increase options could not be read: {type(error).__name__}."
            return {"status": "incomplete", "details": details}
        details["visible_options"] = options
        if options.count("Never") == 1:
            details["message"] = 'The native rate-increase control exposes the exact "Never" option.'
            return {"status": "complete", "details": details}
        details["message"] = 'The native rate-increase control did not expose one exact "Never" option.'
        return {"status": "incomplete", "details": details}

    try:
        await _click(page, toggles[0])
        await _settle_profile_highlight_view(page)
        options = await _visible_texts(page, _DURATION_MENU_OPTIONS)
        details["visible_options"] = options
        await page.keyboard.press("Escape")
        await _settle_profile_highlight_view(page)
        details["menu_dismissed"] = not bool(await _visible_texts(page, _DURATION_MENU_OPTIONS))
    except Exception as error:
        details["menu_dismissed"] = False
        details["message"] = f"Rate-increase menu inspection failed: {type(error).__name__}."
        return {"status": "incomplete", "details": details}
    if options.count("Never") == 1 and details["menu_dismissed"]:
        details["message"] = 'The rate-increase menu exposes the exact "Never" option.'
        return {"status": "complete", "details": details}
    details["message"] = 'The rate-increase menu did not expose one exact "Never" option and dismiss cleanly.'
    return {"status": "incomplete", "details": details}


_PROFILE_HIGHLIGHT_OPENER = (
    'button:has-text("Add profile highlights"), '
    'button:has-text("Add a portfolio project"), '
    'button:has-text("Add an Upwork job"), '
    'button:has-text("Add a certificate"), '
    'button[data-test*="add"][data-test*="profile-highlight"]'
)
_PROFILE_HIGHLIGHT_CHOOSER = (
    '[role="dialog"]:has-text("Add profile highlights"), '
    '.air3-modal:has-text("Add profile highlights"), '
    '.is-modal-fullscreen:has-text("Add profile highlights")'
)
_PROFILE_HIGHLIGHT_TABS = (
    'button[role="tab"][data-ev-tab], '
    '[role="dialog"] button[role="tab"], '
    '.is-modal-fullscreen button[role="tab"]'
)
_PROFILE_HIGHLIGHT_SELECT_BUTTONS = (
    'button:text-is("Select highlight"), '
    'button[data-test*="select-highlight"], '
    '[data-test*="profile-highlight"] button:text-is("Select")'
)
_PROFILE_HIGHLIGHT_CLOSE = (
    '[role="dialog"] button[aria-label="Close"], '
    '[role="dialog"] button[aria-label*="close" i], '
    '.is-modal-fullscreen button[aria-label="Close"], '
    '.is-modal-fullscreen button:has-text("Cancel"), '
    '[role="dialog"] button:has-text("Cancel")'
)
_REQUIRED_PROFILE_HIGHLIGHT_TABS = {"portfolio", "certifications", "upwork_jobs"}


def _normalize_visible_title(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _canonical_highlight_tab(identity: str) -> str | None:
    token = re.sub(r"[^a-z0-9]+", "_", identity.casefold()).strip("_")
    if "portfolio" in token:
        return "portfolio"
    if "cert" in token:
        return "certifications"
    if "job" in token or "work_history" in token or "workhistory" in token:
        return "upwork_jobs"
    return None


async def _element_is_visible(element) -> bool:
    try:
        return bool(await element.is_visible())
    except Exception:
        # Older mocks and some detached handles do not expose visibility. The
        # surrounding selector is already visibility-oriented, so keep reading.
        return True


async def _highlight_title_for_button(button) -> str | None:
    """Read the title belonging to one Select highlight button without clicking it."""

    script = r"""button => {
      const clean = value => (value || '').replace(/\s+/g, ' ').trim();
      const visible = element => {
        if (!element) return false;
        const style = window.getComputedStyle(element);
        return style.display !== 'none' && style.visibility !== 'hidden';
      };
      const usable = value => {
        const text = clean(value);
        return text && text.length <= 300
          && !/^(select highlight|selected|add to highlights|view details)$/i.test(text)
          && !/^add profile highlights$/i.test(text);
      };

      const ariaLabel = clean(button.getAttribute('aria-label'));
      const ariaMatch = ariaLabel.match(/^select highlight(?: for)?\s*[:\-]?\s*(.+)$/i);
      if (ariaMatch && usable(ariaMatch[1])) return clean(ariaMatch[1]);

      const labelledBy = clean(button.getAttribute('aria-labelledby'));
      for (const id of labelledBy.split(' ')) {
        const labelled = document.getElementById(id);
        const text = clean(labelled && labelled.innerText);
        if (usable(text)) return text;
      }

      let card = button.parentElement;
      for (let depth = 0; depth < 12 && card; depth += 1, card = card.parentElement) {
        const selectButtons = [...card.querySelectorAll('button')].filter(candidate =>
          /^select highlight$/i.test(clean(candidate.innerText)) && visible(candidate)
        );
        if (selectButtons.length !== 1) continue;

        const preferred = card.querySelectorAll(
          '[data-test*="title"], [data-test*="name"], [data-qa*="title"], '
          '[role="heading"], h1, h2, h3, h4, h5, h6'
        );
        for (const candidate of preferred) {
          const text = clean(candidate.innerText);
          if (candidate !== button && visible(candidate) && usable(text)) return text;
        }

        const buttonText = new Set(
          [...card.querySelectorAll('button')].flatMap(candidate =>
            (candidate.innerText || '').split('\n').map(clean).filter(Boolean)
          )
        );
        const generic = /^(portfolio project|portfolio|upwork job|certificate|certification)$/i;
        for (const line of (card.innerText || '').split('\n').map(clean)) {
          if (usable(line) && !buttonText.has(line) && !generic.test(line)) return line;
        }
      }
      return null;
    }"""
    try:
        value = await button.evaluate(script)
    except Exception:
        return None
    if not isinstance(value, str):
        return None
    title = _normalize_visible_title(value)
    return title or None


async def _visible_profile_highlight_options(page) -> tuple[list[str], int]:
    """Return normalized visible titles and the visible selectable-card count."""

    titles: list[str] = []
    option_count = 0
    for button in await page.query_selector_all(_PROFILE_HIGHLIGHT_SELECT_BUTTONS):
        if not await _element_is_visible(button):
            continue
        label = _normalize_visible_title((await button.text_content()) or "")
        if label and not re.search(r"select highlight", label, re.I):
            continue
        option_count += 1
        title = await _highlight_title_for_button(button)
        if title:
            titles.append(title)
    return _dedupe_text(titles), option_count


async def _profile_highlight_tabs(page) -> list[tuple[str, Any]]:
    tabs: list[tuple[str, Any]] = []
    seen: set[str] = set()
    for index, tab in enumerate(await page.query_selector_all(_PROFILE_HIGHLIGHT_TABS)):
        if not await _element_is_visible(tab):
            continue
        try:
            tab_id = _normalize_visible_title((await tab.get_attribute("data-ev-tab")) or "")
        except Exception:
            tab_id = ""
        label = _normalize_visible_title((await tab.text_content()) or "")
        raw_identity = tab_id or label or f"tab-{index + 1}"
        identity = _canonical_highlight_tab(raw_identity) or raw_identity
        if identity not in seen:
            seen.add(identity)
            tabs.append((identity, tab))
    return tabs


async def _profile_highlight_chooser_visible(page) -> bool:
    chooser = await page.query_selector(_PROFILE_HIGHLIGHT_CHOOSER)
    if chooser and await _element_is_visible(chooser):
        return True
    _, option_count = await _visible_profile_highlight_options(page)
    return option_count > 0


async def _settle_profile_highlight_view(page) -> None:
    try:
        await page.wait_for_timeout(100)
    except Exception:
        await asyncio.sleep(0)


async def _wait_for_profile_highlight_chooser(page) -> None:
    try:
        await page.wait_for_selector(_PROFILE_HIGHLIGHT_CHOOSER, state="visible", timeout=3000)
    except Exception:
        await _settle_profile_highlight_view(page)


async def _dismiss_profile_highlight_chooser(page) -> bool:
    close_button = await page.query_selector(_PROFILE_HIGHLIGHT_CLOSE)
    if close_button and await _element_is_visible(close_button):
        try:
            await _click(page, close_button)
            await _settle_profile_highlight_view(page)
            if not await _profile_highlight_chooser_visible(page):
                return True
        except Exception:
            pass
    try:
        await page.keyboard.press("Escape")
    except Exception:
        return False
    await _settle_profile_highlight_view(page)
    return not await _profile_highlight_chooser_visible(page)


async def _inspect_available_profile_highlights(page) -> dict[str, Any]:
    """Enumerate every visible chooser tab without selecting or committing anything."""

    result: dict[str, Any] = {
        "titles": [],
        "status": "unavailable",
        "details": {
            "chooser_opened": False,
            "chooser_dismissed": True,
            "tabs_found": [],
            "tabs_inspected": [],
            "required_tabs": sorted(_REQUIRED_PROFILE_HIGHLIGHT_TABS),
            "missing_required_tabs": sorted(_REQUIRED_PROFILE_HIGHLIGHT_TABS),
            "missing_inspected_tabs": sorted(_REQUIRED_PROFILE_HIGHLIGHT_TABS),
            "selectable_options_seen": 0,
            "titles_extracted": 0,
            "message": "The live profile-highlight chooser was not inspected.",
        },
    }
    open_button = await page.query_selector(_PROFILE_HIGHLIGHT_OPENER)
    if not open_button:
        result["details"]["message"] = "The live profile-highlight chooser control was not found."
        return result

    chooser_opened = False
    opener_clicked = False
    try:
        await _click(page, open_button)
        opener_clicked = True
        await _wait_for_profile_highlight_chooser(page)
        tabs = await _profile_highlight_tabs(page)
        _, first_option_count = await _visible_profile_highlight_options(page)
        chooser_opened = bool(tabs or first_option_count or await _profile_highlight_chooser_visible(page))
        result["details"]["chooser_opened"] = chooser_opened
        result["details"]["tabs_found"] = [identity for identity, _ in tabs]
        found_required_tabs = _REQUIRED_PROFILE_HIGHLIGHT_TABS.intersection(
            result["details"]["tabs_found"]
        )
        result["details"]["missing_required_tabs"] = sorted(
            _REQUIRED_PROFILE_HIGHLIGHT_TABS - found_required_tabs
        )
        if not chooser_opened:
            result["details"]["message"] = "The profile-highlight chooser did not open."
            return result

        titles: list[str] = []
        selectable_options_seen = 0
        unresolved_options = 0
        tab_failures: list[str] = []
        views = tabs or [("current_view", None)]
        for identity, tab in views:
            if tab is not None:
                try:
                    await _click(page, tab)
                    await _settle_profile_highlight_view(page)
                except Exception:
                    refreshed = dict(await _profile_highlight_tabs(page)).get(identity)
                    if refreshed is None:
                        tab_failures.append(identity)
                        continue
                    try:
                        await _click(page, refreshed)
                        await _settle_profile_highlight_view(page)
                    except Exception:
                        tab_failures.append(identity)
                        continue
            view_titles, view_option_count = await _visible_profile_highlight_options(page)
            titles.extend(view_titles)
            selectable_options_seen += view_option_count
            unresolved_options += max(0, view_option_count - len(view_titles))
            result["details"]["tabs_inspected"].append(identity)

        result["titles"] = _dedupe_text(titles)
        result["details"]["selectable_options_seen"] = selectable_options_seen
        result["details"]["titles_extracted"] = len(result["titles"])
        missing_required_tabs = result["details"]["missing_required_tabs"]
        inspected_required_tabs = _REQUIRED_PROFILE_HIGHLIGHT_TABS.intersection(
            result["details"]["tabs_inspected"]
        )
        missing_inspected_tabs = sorted(_REQUIRED_PROFILE_HIGHLIGHT_TABS - inspected_required_tabs)
        result["details"]["missing_inspected_tabs"] = missing_inspected_tabs
        if tab_failures or unresolved_options or missing_required_tabs or missing_inspected_tabs:
            result["status"] = "incomplete"
            reasons = []
            if tab_failures:
                reasons.append(f"tabs could not be inspected: {', '.join(tab_failures)}")
            if unresolved_options:
                reasons.append(f"{unresolved_options} selectable option titles could not be read")
            if missing_required_tabs:
                reasons.append(
                    "required tabs were not visible: " + ", ".join(missing_required_tabs)
                )
            if missing_inspected_tabs:
                reasons.append("required tabs were not inspected: " + ", ".join(missing_inspected_tabs))
            result["details"]["message"] = "Live profile-highlight enumeration is incomplete: " + "; ".join(reasons)
        else:
            result["status"] = "complete"
            result["details"]["message"] = (
                "Portfolio, certifications, and Upwork-jobs tabs were all enumerated."
            )
    except Exception as error:
        result["status"] = "incomplete" if chooser_opened else "unavailable"
        result["details"]["message"] = f"Profile-highlight enumeration failed: {type(error).__name__}."
    finally:
        if opener_clicked:
            dismissed = await _dismiss_profile_highlight_chooser(page)
            result["details"]["chooser_dismissed"] = dismissed
            if not dismissed:
                result["status"] = "incomplete"
                result["details"]["message"] = (
                    "Live profile-highlight enumeration is incomplete because the chooser could not be dismissed."
                )
    return result


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

    _, expected_job_id, _ = parse_job_or_application_url(params.job_url)
    canonical_job_url, _ = parse_job_url(f"https://www.upwork.com/jobs/{expected_job_id}")
    form_status, existing_evidence = await _open_proposal_form(page, params.job_url)
    text = await _page_text(page)
    identity = await _application_identity_from_current_page(page) if form_status == "ready" else None
    if form_status == "ready" and (
        identity is None or identity["job_id"] != expected_job_id
    ):
        form_status = "identity_mismatch"
        identity = None

    job_type = (
        cast(Literal["hourly", "fixed"], identity["job_type"])
        if identity
        else None
    )
    fixed_payment_structures = (
        await _inspect_fixed_payment_structures(page)
        if form_status == "ready" and job_type == "fixed"
        else []
    )

    fee_net_inspection = await _inspect_fee_net_state(page, text)
    boost_auction_inspection = await _inspect_boost_auction_state(page, text)
    if form_status == "ready":
        question_inspection = await _inspect_screening_questions(page)
        highlight_inspection = await _inspect_available_profile_highlights(page)
        duration_inspection = await _inspect_duration_options(page)
        rate_increase_inspection = await _inspect_rate_increase_control(page, job_type)
    else:
        question_inspection = {
            "questions": [],
            "status": "unavailable",
            "details": {
                "message": "The proposal form is not ready, so screening questions were not inspected."
            },
        }
        highlight_inspection = {
            "titles": [],
            "status": "unavailable",
            "details": {
                "chooser_opened": False,
                "chooser_dismissed": True,
                "tabs_found": [],
                "tabs_inspected": [],
                "required_tabs": sorted(_REQUIRED_PROFILE_HIGHLIGHT_TABS),
                "missing_required_tabs": sorted(_REQUIRED_PROFILE_HIGHLIGHT_TABS),
                "missing_inspected_tabs": sorted(_REQUIRED_PROFILE_HIGHLIGHT_TABS),
                "selectable_options_seen": 0,
                "titles_extracted": 0,
                "message": "The proposal form is not ready, so profile highlights were not inspected.",
            },
        }
        duration_inspection = {
            "options": [],
            "status": "unavailable",
            "details": {
                "message": "The proposal form is not ready, so duration options were not inspected."
            },
        }
        rate_increase_inspection = {
            "status": "unavailable",
            "details": {
                "message": "The proposal form is not ready, so rate-increase applicability was not inspected."
            },
        }
        fee_net_inspection = {
            "text": fee_net_inspection["text"],
            "status": "unavailable",
            "details": {
                **fee_net_inspection["details"],
                "message": "The proposal form is not ready, so fee/net context is unavailable.",
            },
        }
        boost_auction_inspection = {
            "text": boost_auction_inspection["text"],
            "status": "unavailable",
            "details": {
                **boost_auction_inspection["details"],
                "message": "The proposal form is not ready, so boost-auction context is unavailable.",
            },
        }

    return {
        "job_url": canonical_job_url,
        "job_id": expected_job_id,
        "form_url": identity["form_url"] if identity else None,
        "job_title": identity["job_title"] if identity else None,
        "form_status": form_status,
        "existing_proposal": existing_evidence is not None,
        "existing_proposal_evidence": existing_evidence,
        "screening_questions": question_inspection["questions"],
        "screening_questions_status": question_inspection["status"],
        "screening_questions_details": question_inspection["details"],
        "job_type": job_type,
        "fixed_payment_structures": fixed_payment_structures,
        "base_connects": _extract_base_connects(text),
        "fee_net_text": fee_net_inspection["text"],
        "fee_net_status": fee_net_inspection["status"],
        "fee_net_details": fee_net_inspection["details"],
        "duration_options": duration_inspection["options"],
        "duration_options_status": duration_inspection["status"],
        "duration_options_details": duration_inspection["details"],
        "boost_auction_text": boost_auction_inspection["text"],
        "boost_auction_status": boost_auction_inspection["status"],
        "boost_auction_details": boost_auction_inspection["details"],
        "available_profile_highlights": highlight_inspection["titles"],
        "available_profile_highlights_status": highlight_inspection["status"],
        "available_profile_highlights_details": highlight_inspection["details"],
        "rate_increase_control_status": rate_increase_inspection["status"],
        "rate_increase_control_details": rate_increase_inspection["details"],
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


async def _checked_state(element) -> bool | None:
    try:
        return bool(await element.is_checked())
    except Exception:
        try:
            value = await element.get_attribute("aria-checked")
        except Exception:
            return None
        if value == "true":
            return True
        if value == "false":
            return False
        return None


async def _fixed_payment_section(page) -> Any | None:
    """Resolve one payment section containing both exact structure labels."""

    candidates = await page.query_selector_all(
        'fieldset:has(label:text-is("By project")):has(label:text-is("By milestone")), '
        '[data-test="payment-terms"]:has-text("By project"):has-text("By milestone"), '
        '[data-test="payment-structure"]:has-text("By project"):has-text("By milestone")'
    )
    exact: list[Any] = []
    for candidate in candidates:
        try:
            text = _normalise_identity_text(await candidate.text_content())
        except Exception:
            continue
        if re.search(r"(?:^|\s)By project(?:\s|$)", text) and re.search(
            r"(?:^|\s)By milestone(?:\s|$)", text
        ):
            exact.append(candidate)
    return exact[0] if len(exact) == 1 else None


async def _exact_payment_radio(section, label: str):
    token = "project" if label == "By project" else "milestone"
    matches = await section.query_selector_all(
        ", ".join(
            (
                f'label:text-is("{label}") input[type="radio"]',
                f'input[type="radio"][aria-label="{label}"]',
                f'input[type="radio"][value="{token}"]',
                f'input[type="radio"][value="by_{token}"]',
                f'input[type="radio"][data-test="by-{token}"]',
            )
        )
    )
    if len(matches) != 1:
        return None
    return matches[0]


async def _inspect_fixed_payment_structures(
    page,
) -> list[Literal["by_project", "by_milestone"]]:
    """Read exact fixed-price options from one scoped payment section."""

    section = await _fixed_payment_section(page)
    if section is None:
        return []
    project = await _exact_payment_radio(section, "By project")
    milestone = await _exact_payment_radio(section, "By milestone")
    if project is None or milestone is None:
        return []
    return ["by_project", "by_milestone"]


async def _select_fixed_payment_structure(
    page,
    structure: Literal["by_project", "by_milestone"],
) -> Any | None:
    """Select and read back one exact fixed-price payment structure."""

    section = await _fixed_payment_section(page)
    if section is None:
        return None
    label = "By project" if structure == "by_project" else "By milestone"
    opposite_label = "By milestone" if structure == "by_project" else "By project"
    radio = await _exact_payment_radio(section, label)
    opposite = await _exact_payment_radio(section, opposite_label)
    if radio is None or opposite is None:
        return None
    checked = await _checked_state(radio)
    opposite_checked = await _checked_state(opposite)
    if checked is None or opposite_checked is None:
        return None
    if not checked:
        try:
            await _click(page, radio)
        except Exception:
            return None
    if await _checked_state(radio) is not True or await _checked_state(opposite) is not False:
        return None
    return section


async def _fill_one_exact_input(page, selectors: str, value: str) -> bool:
    inputs: list[Any] = []
    for element in await page.query_selector_all(selectors):
        try:
            enabled = bool(await element.is_enabled())
        except Exception:
            enabled = True
        if enabled:
            inputs.append(element)
    if len(inputs) != 1:
        return False
    element = inputs[0]
    try:
        await element.fill(value)
        live_value = str(await element.input_value()).replace(",", "").replace("$", "").strip()
    except Exception:
        return False
    try:
        return Decimal(live_value) == Decimal(value)
    except Exception:
        return False


async def _configure_fixed_payment_terms(page, params: SubmitProposalParams) -> tuple[bool, str | None]:
    """Apply only the exact owner-approved fixed-price structure and terms."""

    if params.payment_structure is None or params.bid is None:
        return False, "Fixed-price payment terms were not approval-bound"
    section = await _select_fixed_payment_structure(page, params.payment_structure)
    if section is None:
        return False, "Approved fixed-price payment structure could not be selected and verified"

    if params.payment_structure == "by_project":
        ok = await _fill_one_exact_input(
            section,
            '[data-test="bid-input"], input[name="bid"], input[name="amount"], '
            'input[data-test="project-amount"], input[placeholder="$0.00"]',
            str(params.bid),
        )
        return (True, None) if ok else (False, "One exact by-project total input could not be filled and verified")

    rows = await section.query_selector_all(
        '[data-test="milestone-row"], [data-test*="milestone-item"], .milestone-row'
    )
    if len(rows) != len(params.milestones):
        return False, "Live milestone rows differ from the exact approved milestones"
    for row, milestone in zip(rows, params.milestones, strict=True):
        description = await row.query_selector(
            'input[name*="description"], textarea[name*="description"], [data-test*="description"] input'
        )
        due_date = await row.query_selector(
            'input[name*="due"], input[name*="date"], [data-test*="due-date"] input'
        )
        amount = await row.query_selector(
            'input[name*="amount"], input[data-test*="amount"]'
        )
        if not description or not due_date or not amount:
            return False, "An exact approved milestone field could not be found"
        try:
            await description.fill(milestone.description)
            await due_date.fill(milestone.due_date)
            await amount.fill(str(milestone.amount))
            live_description = _normalise_identity_text(await description.input_value())
            live_due_date = str(await due_date.input_value()).strip()
            live_amount = Decimal(
                str(await amount.input_value()).replace(",", "").replace("$", "").strip()
            )
        except Exception:
            return False, "Approved milestone values could not be read back"
        if (
            live_description != milestone.description
            or live_due_date != milestone.due_date
            or live_amount != Decimal(str(milestone.amount))
        ):
            return False, "Live milestone values differ from the exact approved milestones"
    return True, None


async def _acknowledge_fixed_price_warning(page) -> bool:
    """Acknowledge only the exact fixed-price warning dialog, if it is present."""

    matching_dialogs: list[Any] = []
    try:
        dialogs = await page.query_selector_all('[role="dialog"]')
    except Exception:
        return False
    for dialog in dialogs:
        try:
            dialog_text = _normalise_identity_text(await dialog.text_content())
        except Exception:
            continue
        if re.search(r"\b3 things you need to know\b", dialog_text, re.I) and re.search(
            r"\bYes, I understand\.?\b", dialog_text, re.I
        ):
            matching_dialogs.append(dialog)
    if len(matching_dialogs) != 1:
        return False
    dialog = matching_dialogs[0]
    try:
        acknowledgements = await dialog.query_selector_all(
            'label:text-is("Yes, I understand") input[type="checkbox"], '
            'label:text-is("Yes, I understand.") input[type="checkbox"], '
            'input[type="checkbox"][aria-label="Yes, I understand"], '
            'input[type="checkbox"][aria-label="Yes, I understand."]'
        )
        continue_buttons = await dialog.query_selector_all('button:text-is("Continue")')
    except Exception:
        return False
    if len(acknowledgements) != 1 or len(continue_buttons) != 1:
        return False
    acknowledgement = acknowledgements[0]
    continue_btn = continue_buttons[0]
    try:
        await acknowledgement.check()
    except Exception:
        try:
            await _click(page, acknowledgement)
        except Exception:
            return False
    if await _checked_state(acknowledgement) is not True:
        return False
    try:
        await _click(page, continue_btn)
    except Exception:
        return False
    return True


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


def _success_query_is_true(url: str) -> bool:
    parsed = urlparse(url)
    return any(
        part.casefold() in {"success", "success=true", "success=1"}
        for part in parsed.query.split("&")
        if part
    )


def _readback_price(value: Any) -> Decimal | None:
    """Read one unambiguous monetary amount from a stored proposal field."""

    matches = re.findall(r"(?:\$\s*)?([0-9][0-9,]*(?:\.[0-9]{1,2})?)", str(value or ""))
    try:
        amounts = {Decimal(match.replace(",", "")).quantize(Decimal("0.01")) for match in matches}
    except Exception:
        return None
    return next(iter(amounts)) if len(amounts) == 1 else None


async def _proposal_confirmation(
    page,
    approved_target: Mapping[str, Any],
    timeout_seconds: float = 15,
) -> dict[str, Any]:
    """Confirm submission only by reading one exact stored proposal identity."""

    for _ in range(max(1, int(timeout_seconds * 2))):
        try:
            url = str(getattr(page, "url", ""))
        except Exception:
            url = ""
        try:
            text = await _page_text(page)
        except Exception:
            text = ""
        success_text = re.search(
            r"your proposal was submitted|proposal submitted successfully|proposal has been submitted",
            text,
            re.I,
        )
        try:
            proposal_url, proposal_id = parse_submitted_proposal_url(url)
        except ValueError:
            proposal_url = ""
            proposal_id = ""
        if proposal_id:
            try:
                details = await _get_proposal_details_on_page(proposal_url, page)
            except Exception:
                details = {}
            live_title = _normalise_identity_text(details.get("job_title"))
            live_status = _normalise_identity_text(details.get("status")).casefold()
            live_cover_letter = _normalise_identity_text(details.get("cover_letter"))
            live_price = _readback_price(details.get("bid"))
            try:
                approved_price = Decimal(str(approved_target["price_amount"]))
                approved_cover_letter = str(approved_target["cover_letter"])
            except (KeyError, ArithmeticError, ValueError):
                approved_price = None
                approved_cover_letter = ""
            same_target = bool(
                details.get("proposal_id") == proposal_id
                and details.get("job_id") == approved_target["job_id"]
                and details.get("job_url") == approved_target["job_url"]
                and live_title == approved_target["job_title"]
                and live_status in {"active", "submitted"}
                and live_cover_letter == approved_cover_letter
                and approved_price is not None
                and live_price == approved_price
            )
            if same_target:
                return {
                    "confirmed": True,
                    "url": proposal_url,
                    "proposal_id": proposal_id,
                    "job_id": approved_target["job_id"],
                    "job_title": live_title,
                    "price_amount": str(live_price),
                    "proposal_status": live_status,
                    "evidence": (
                        success_text.group(0)
                        if success_text
                        else "exact stored proposal identity"
                    ),
                    "success_query": _success_query_is_true(url),
                }
        await asyncio.sleep(0.5)
    try:
        final_url = str(getattr(page, "url", ""))
    except Exception:
        final_url = ""
    return {
        "confirmed": False,
        "url": final_url,
        "evidence": None,
        "success_query": _success_query_is_true(final_url),
    }


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
        approved=False,
        approval_sha256=None,
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

    form_status, existing_evidence = await _open_proposal_form(page, params.form_url)
    if form_status == "already_applied":
        return {
            "status": "already_submitted",
            "message": existing_evidence,
            "external_action_taken": False,
        }
    if form_status == "identity_mismatch":
        return {
            "status": "live_identity_mismatch",
            "message": (
                "Upwork did not remain on the exact approved application route; "
                "nothing was filled or submitted. Prepare the current job again before new approval."
            ),
            "external_action_taken": False,
        }
    if form_status != "ready":
        return {
            "status": "error",
            "message": "Apply form not found. Job may be closed or unavailable.",
            "external_action_taken": False,
        }

    approved_identity = {
        "job_url": params.job_url,
        "job_id": params.job_id,
        "form_url": params.form_url,
        "job_title": params.job_title,
        "job_type": params.job_type,
    }
    live_identity = await _application_identity_from_current_page(page)
    if live_identity != approved_identity:
        return {
            "status": "live_identity_mismatch",
            "message": (
                "The live application identity differs from the approved payload; "
                "nothing was filled or submitted. Prepare the current job again before new approval."
            ),
            "approved_application_identity": approved_identity,
            "live_application_identity": live_identity,
            "external_action_taken": False,
        }
    approved_proposal_target: dict[str, Any] = {
        **approved_identity,
        "cover_letter": _normalise_identity_text(params.cover_letter),
        "price_amount": str(
            Decimal(str(params.rate if params.rate is not None else params.bid)).quantize(
                Decimal("0.01")
            )
        ),
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

    # Only after exact identity readback may form controls be queried or filled.
    if params.rate is not None:
        rate_input = await _first_enabled(page, '[data-test="hourly-rate-input"], input[name*="rate"]')
        if not rate_input:
            return {"status": "error", "message": "Hourly rate input not found", "external_action_taken": False}
        await rate_input.fill(str(params.rate))

    if params.bid is not None:
        fixed_ok, fixed_error = await _configure_fixed_payment_terms(page, params)
        if not fixed_ok:
            return {"status": "error", "message": fixed_error, "external_action_taken": False}

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

    immediate_confirmation = await _proposal_confirmation(
        page,
        approved_proposal_target,
        timeout_seconds=2,
    )
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

    if params.bid is not None:
        await _acknowledge_fixed_price_warning(page)

    readback = await _proposal_confirmation(
        page,
        approved_proposal_target,
    )
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
