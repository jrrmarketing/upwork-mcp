"""Proposal tools for Upwork MCP.

Every consequential action in this module is approval-gated before the browser is
created. Proposal submission accepts only an approved one-time prepared action;
other guarded actions retain their exact-payload preparation interfaces.
"""

from __future__ import annotations

import asyncio
import hashlib
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
_SUBMITTED_PROPOSAL_TITLE_SELECTOR = '[data-test="job-title"], .job-title'
_SUBMITTED_PROPOSAL_STATUS_SELECTOR = (
    '[data-test="proposal-status"], .proposal-status, [data-test*="proposal-status"]'
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
    """Authorize only an approved, one-shot prepared action.

    ``approved`` and ``approval_sha256`` remain accepted at this shared boundary
    solely so older callers receive a fail-closed response instead of a runtime
    error.  A reusable digest is not authorization for an owner-system mutation.
    """

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
    prepared["message"] = (
        "No browser was opened. Prepare this exact payload, obtain owner approval, "
        "and retry with its approved one-time action_id. Legacy approved/digest "
        "authorization is not accepted."
    )
    if approved or approval_sha256:
        prepared["legacy_authorization_rejected"] = True
    return prepared


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


class InspectProposalCommercialPreflightParams(StrictToolModel):
    """Exact price used for a reversible, non-submitting fee/net preflight."""

    job_url: str = Field(description="Full individual Upwork job or application-form URL")
    rate: float | None = Field(default=None, ge=50)
    bid: float | None = Field(default=None, gt=0)
    payment_structure: Literal["by_project"] | None = None

    _validate_job_url = field_validator("job_url")(validate_job_or_application_url)

    @model_validator(mode="after")
    def _validate_reversible_terms(self) -> InspectProposalCommercialPreflightParams:
        if (self.rate is None) == (self.bid is None):
            raise ValueError("Provide exactly one of rate or bid for commercial preflight")
        amount = Decimal(str(self.rate if self.rate is not None else self.bid))
        if not amount.is_finite() or amount != amount.quantize(Decimal("0.01")):
            raise ValueError("Commercial preflight prices cannot use fractions of a cent")
        if self.rate is not None and self.payment_structure is not None:
            raise ValueError("Hourly commercial preflight cannot include a payment structure")
        if self.bid is not None and self.payment_structure != "by_project":
            raise ValueError(
                "Only reversible by-project fixed-price commercial preflight is supported"
            )
        return self


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
    fee_net_price_amount: str = Field(
        pattern=r"^[0-9]+\.[0-9]{2}$",
        description="Exact approved rate/bid used to produce the scoped fee/net preview",
    )
    fee_net_source: Literal["scoped_reversible_price_preflight"] = Field(
        description="Provenance of the approval-bound fee/net preview",
    )
    boost_auction_text: list[str] = Field(
        description="Normalized live boost-auction context shown during preparation",
    )
    boost_auction_status: DiscoveryStatus = Field(
        description="Completeness of the live boost-auction inspection bound during preparation",
    )
    rate: float | None = Field(
        default=None,
        ge=50,
        description="Proposed hourly rate; $50 is the owner-approved absolute floor",
    )
    bid: float | None = Field(default=None, gt=0, description="Fixed-price bid")
    payment_structure: Literal["by_project", "by_milestone"] | None = Field(
        default=None,
        description="Required explicit structure for fixed-price proposals",
    )
    milestones: list[FixedPriceMilestone] = Field(default_factory=list, max_length=1)
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
    base_connects_status: DiscoveryStatus = Field(
        description="Completeness of exact scoped base-Connect control inspection",
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

    @field_validator("profile_highlights")
    @classmethod
    def _profile_highlights_must_be_distinct(cls, values: list[str]) -> list[str]:
        identities = [re.sub(r"\s+", " ", value).strip().casefold() for value in values]
        if len(identities) != len(set(identities)):
            raise ValueError("Profile highlights cannot contain duplicates")
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
        approved_price = Decimal(str(self.rate if self.rate is not None else self.bid)).quantize(
            Decimal("0.01")
        )
        if Decimal(self.fee_net_price_amount) != approved_price:
            raise ValueError("fee_net_price_amount must exactly match the approved rate or bid")
        required_complete = {
            "fee/net": self.fee_net_status,
            "base Connects": self.base_connects_status,
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
        if self.base_connects is None:
            raise ValueError("Complete base-Connect inspection requires an exact live cost")
        if len(self.screening_questions) != len(self.answers or []):
            raise ValueError("Screening questions and exact approved answers must have equal counts")
        if self.boost_auction_status == "complete" and not self.boost_auction_text:
            raise ValueError("Complete boost-auction inspection requires normalized live auction text")
        if self.boost_auction_status == "unavailable" and self.boost_auction_text:
            raise ValueError("Unavailable boost-auction inspection cannot include live auction text")
        if self.boost_connects:
            raise ValueError(
                "Automatic positive boost submission is disabled until the live Upwork flow "
                "can prove the first Submit click is non-consequential"
            )
        if self.rate_increase_control_status not in {"complete", "not_applicable"}:
            raise ValueError(
                "Rate-increase control inspection must be complete or explicitly not_applicable"
            )
        if self.job_type == "fixed" and self.rate_increase_control_status != "not_applicable":
            raise ValueError("Fixed-price proposals require rate_increase_control_status=not_applicable")
        if self.job_type == "hourly" and self.rate_increase_control_status != "complete":
            raise ValueError("Hourly proposals require a complete live rate-increase control")
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
        "fee_net_price_amount": params.fee_net_price_amount,
        "fee_net_source": params.fee_net_source,
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
        "base_connects": params.base_connects,
        "base_connects_status": params.base_connects_status,
        "boost_connects": params.boost_connects,
        "rate_increase_frequency": params.rate_increase_frequency,
        "rate_increase_control_status": params.rate_increase_control_status,
    }
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

    async def one_exact_visible_text(selector: str) -> str | None:
        try:
            candidates = await page.query_selector_all(selector)
        except Exception:
            return None
        visible = [candidate for candidate in candidates if await _element_is_visible(candidate)]
        if len(visible) != 1:
            return None
        try:
            value = _normalise_identity_text(await visible[0].text_content())
        except Exception:
            return None
        return value or None

    # Identity and owner status must come from unique visible scoped controls.
    job_title = await one_exact_visible_text(_SUBMITTED_PROPOSAL_TITLE_SELECTOR)
    if job_title:
        details["job_title"] = job_title
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

    proposal_status = await one_exact_visible_text(_SUBMITTED_PROPOSAL_STATUS_SELECTOR)
    if proposal_status:
        details["status"] = proposal_status

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


def _proposal_status_is_withdrawn(value: str) -> bool:
    """Match only an exact withdrawn state read from the scoped status control."""

    return bool(
        re.fullmatch(
            r"(?:proposal\s+)?withdrawn(?:\s+proposal)?",
            re.sub(r"\s+", " ", value).strip(),
            re.I,
        )
    )


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
    elif _proposal_status_is_withdrawn(identity["proposal_status"]):
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
    """Click one visible, enabled control, including the overlay fallback."""

    if not await _element_is_visible(element) or not await _element_is_enabled(element):
        raise RuntimeError("Refusing to click a control that is not visible and enabled")
    try:
        await element.click()
    except Exception as error:
        # Keep the historical overlay fallback, but perform the actionability
        # check and DOM click in one browser-side operation.  A hidden clone or
        # a control that changed state after resolution must never be clicked.
        clicked = await page.evaluate(
            """element => {
              const style = window.getComputedStyle(element);
              const visible = Boolean(
                element.isConnected
                && element.getClientRects().length
                && style.display !== 'none'
                && style.visibility !== 'hidden'
              );
              const enabled = !element.matches(':disabled')
                && element.getAttribute('aria-disabled') !== 'true';
              if (!visible || !enabled) return false;
              element.click();
              return true;
            }""",
            element,
        )
        if clicked is not True:
            raise RuntimeError("The control stopped being visible and enabled before click") from error


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

    amount_pattern = r"(?:[$£€]\s*\d[\d,.]*|\d[\d,]*\.\d{2}\b|\d[\d,.]*\s*(?:USD|GBP|EUR)\b)"
    net_preview_pattern = (
        rf"you(?:'|’)?ll receive\s*[:\-]?\s*{amount_pattern}|"
        rf"you will receive\s*[:\-]?\s*{amount_pattern}|"
        rf"(?:freelancer\s+)?(?:net\s+)?(?:earnings|payment|payout|amount)"
        rf"\s*(?:after fees)?\s*[:\-]?\s*{amount_pattern}|"
        rf"\bnet\s+(?:earnings|payment|payout|amount)\s*[:\-]?\s*{amount_pattern}|"
        r"[$£€]\s*\d[\d,.]*\s+net\b"
    )
    lines = normalize_live_context_lines(
        [
            line
            for line in text.splitlines()
            if re.search(
                rf"service fee|upwork fee|{net_preview_pattern}",
                line,
                re.I,
            )
        ]
    )
    fee_lines = [line for line in lines if re.search(r"service fee|upwork fee", line, re.I)]
    net_lines = [
        line
        for line in lines
        if re.search(net_preview_pattern, line, re.I)
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
            r"\b(?:top|highest|lowest|current|average)\s+bid\b.{0,50}\d+\s+connects?\b|"
            r"\d+\s+connects?\b.{0,50}\b(?:top|highest|lowest|current|average)\s+bid\b|"
            r"\bno\s+bids?\b|\bbe\s+the\s+first\b|"
            r"\brank(?:ed|ing)?\s*#?\s*\d+\b|"
            r"\b(?:1st|2nd|3rd|4th)\s+(?:place|slot)\b|"
            r"\b\d+\s+(?:competing\s+|other\s+)?(?:bids?|bidders?)\b",
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


_FEE_CONTROL_SELECTOR = (
    '[data-test="service-fee"], [data-test="service-fee-amount"], '
    '[data-test="upwork-service-fee"], input[data-test="service-fee"], '
    'input[name="serviceFee"]'
)
_NET_CONTROL_SELECTOR = (
    '[data-test="you-will-receive"], [data-test="you-will-receive-amount"], '
    '[data-test="freelancer-net-earnings"], [data-test="freelancer-earnings"], '
    'input[data-test="you-will-receive"], input[name="youWillReceive"]'
)
_BOOST_AUCTION_CONTROL_SELECTOR = (
    '[data-test="boost-auction"], [data-test="boost-proposal-auction"], '
    '[data-test="boost-proposal-section"], [data-test="boost-bid-context"], '
    '[data-test^="boost-auction-"]'
)
_BASE_CONNECTS_CONTROL_SELECTOR = (
    '[data-test="proposal-connects-cost"], [data-test="submit-proposal-connects"], '
    '[data-test="connects-required"], [data-test="apply-connects-cost"]'
)


async def _scoped_control_lines(
    page,
    selector: str,
    *,
    semantic_label: str | None = None,
) -> tuple[list[str], int]:
    """Read only exact Upwork-owned controls selected by stable semantic attributes."""

    try:
        controls = await page.query_selector_all(selector)
    except Exception:
        return [], 0
    lines: list[str] = []
    visible_count = 0
    for control in controls:
        if not await _element_is_visible(control):
            continue
        visible_count += 1
        try:
            text = _normalise_identity_text(await control.text_content())
        except Exception:
            text = ""
        if not text:
            try:
                text = _normalise_identity_text(await control.input_value())
            except Exception:
                text = ""
        if not text:
            continue
        if semantic_label and semantic_label.casefold() not in text.casefold():
            text = f"{semantic_label}: {text}"
        lines.append(text)
    return _dedupe_text(lines), visible_count


_CURRENCY_MARKER = re.compile(
    r"(?<![A-Za-z])(?:US\$|A\$|C\$|USD|GBP|EUR|AUD|CAD|\$|£|€)(?![A-Za-z])",
    re.I,
)
_CURRENCY_AMOUNT = re.compile(
    r"(?<![\w.])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?(?![\w.,])"
)


def _normalize_currency_marker(value: str) -> str:
    marker = value.upper()
    return {
        "US$": "USD",
        "A$": "AUD",
        "C$": "CAD",
        "£": "GBP",
        "€": "EUR",
    }.get(marker, marker)


def _exact_currency_amount(value: str) -> tuple[Decimal, str] | None:
    """Parse exactly one adjacent currency marker and amount from scoped text."""

    currencies = list(_CURRENCY_MARKER.finditer(value))
    amounts = [
        match
        for match in _CURRENCY_AMOUNT.finditer(value)
        if not value[match.end() :].lstrip().startswith("%")
    ]
    if len(currencies) != 1 or len(amounts) != 1:
        return None
    currency_match = currencies[0]
    amount_match = amounts[0]
    if currency_match.end() <= amount_match.start():
        adjacent = not value[currency_match.end() : amount_match.start()].strip()
    elif amount_match.end() <= currency_match.start():
        adjacent = not value[amount_match.end() : currency_match.start()].strip()
    else:
        adjacent = False
    if not adjacent:
        return None
    try:
        amount = Decimal(amount_match.group(0).replace(",", ""))
    except ArithmeticError:
        return None
    if not amount.is_finite() or amount < 0 or amount != amount.quantize(Decimal("0.01")):
        return None
    return amount.quantize(Decimal("0.01")), _normalize_currency_marker(currency_match.group(0))


async def _inspect_fee_net_state(page) -> dict[str, Any]:
    """Read fee/net evidence only from exact scoped Upwork commercial controls."""

    fee_lines, fee_controls = await _scoped_control_lines(
        page,
        _FEE_CONTROL_SELECTOR,
        semantic_label="Upwork service fee",
    )
    net_lines, net_controls = await _scoped_control_lines(
        page,
        _NET_CONTROL_SELECTOR,
        semantic_label="You'll receive",
    )
    parsed = _inspect_fee_net_context("\n".join([*fee_lines, *net_lines]))
    fee_amount = _exact_currency_amount(fee_lines[0]) if len(fee_lines) == 1 else None
    net_amount = _exact_currency_amount(net_lines[0]) if len(net_lines) == 1 else None
    same_currency = bool(fee_amount and net_amount and fee_amount[1] == net_amount[1])
    if (
        fee_controls == 1
        and net_controls == 1
        and len(fee_lines) == 1
        and len(net_lines) == 1
        and fee_amount is not None
        and net_amount is not None
        and same_currency
    ):
        parsed["status"] = "complete"
        parsed["details"]["message"] = (
            "Exact same-currency fee and freelancer net amounts were read from scoped controls."
        )
    elif fee_controls or net_controls:
        parsed["status"] = "incomplete"
        parsed["details"]["message"] = (
            "Scoped Upwork fee/net controls were present but could not be read unambiguously."
        )
    else:
        parsed = {
            "text": [],
            "status": "unavailable",
            "details": {
                "fee_lines_seen": 0,
                "net_lines_seen": 0,
                "message": "Exact scoped Upwork fee/net controls were unavailable.",
            },
        }
    parsed["details"].update(
        {
            "fee_controls_seen": fee_controls,
            "net_controls_seen": net_controls,
            "fee_amount": format(fee_amount[0], ".2f") if fee_amount else None,
            "net_amount": format(net_amount[0], ".2f") if net_amount else None,
            "fee_currency": fee_amount[1] if fee_amount else None,
            "net_currency": net_amount[1] if net_amount else None,
            "amounts_unambiguous": fee_amount is not None and net_amount is not None,
            "same_currency": same_currency,
            "evidence_scope": "exact_upwork_controls",
        }
    )
    return parsed


async def _inspect_boost_auction_state(page) -> dict[str, Any]:
    """Read auction evidence only from exact scoped Upwork boost controls."""

    lines, controls_seen = await _scoped_control_lines(
        page,
        _BOOST_AUCTION_CONTROL_SELECTOR,
        semantic_label="Boost auction",
    )
    if not controls_seen:
        return {
            "text": [],
            "status": "unavailable",
            "details": {
                "boost_context_seen": False,
                "auction_state_seen": False,
                "controls_seen": 0,
                "evidence_scope": "exact_upwork_controls",
                "message": "Exact scoped Upwork boost-auction controls were unavailable.",
            },
        }
    parsed = _inspect_boost_auction_context("\n".join(lines))
    parsed["details"].update(
        {
            "controls_seen": controls_seen,
            "evidence_scope": "exact_upwork_controls",
        }
    )
    if not lines:
        parsed["status"] = "incomplete"
        parsed["details"]["message"] = (
            "Scoped Upwork boost-auction controls were present but unreadable."
        )
    elif controls_seen != 1:
        parsed["status"] = "incomplete"
        parsed["details"]["message"] = (
            "Multiple scoped Upwork boost-auction controls made live evidence ambiguous."
        )
    return parsed


async def _inspect_base_connects_state(page) -> dict[str, Any]:
    """Read base proposal cost only from exact scoped Upwork cost controls."""

    lines, controls_seen = await _scoped_control_lines(
        page,
        _BASE_CONNECTS_CONTROL_SELECTOR,
        semantic_label="Proposal cost",
    )
    values = {
        value
        for line in lines
        if (value := _extract_base_connects(line)) is not None
    }
    if controls_seen == 1 and len(values) == 1:
        status: DiscoveryStatus = "complete"
        value = next(iter(values))
        message = "One exact base-Connect cost was read from scoped Upwork controls."
    elif controls_seen:
        status = "incomplete"
        value = None
        message = "Scoped Upwork base-Connect controls were present but ambiguous or unreadable."
    else:
        status = "unavailable"
        value = None
        message = "Exact scoped Upwork base-Connect controls were unavailable."
    return {
        "value": value,
        "status": status,
        "text": lines,
        "details": {
            "controls_seen": controls_seen,
            "values_seen": sorted(values),
            "evidence_scope": "exact_upwork_controls",
            "message": message,
        },
    }


def _extract_base_connects(text: str) -> int | None:
    """Extract base proposal cost without confusing it with account balance."""

    if re.search(r"no connects? (?:are )?required|costs? 0 connects?|send for 0 connects?", text, re.I):
        return 0

    relevant_lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in text.splitlines()
        if "connect" in line.lower()
        and (
            any(term in line.lower() for term in ("required", "submit", "cost", "send for"))
            or re.fullmatch(r"\s*\d+\s+connects?\s*", line, re.I)
        )
    ]
    patterns = (
        r"send\s+for\s+(\d+)\s+connects?",
        r"proposal\s+cost\D{0,30}(\d+)\s+connects?",
        r"(?:requires?|required|costs?)\D{0,30}(\d+)\s+connects?",
        r"(\d+)\s+connects?\D{0,30}(?:required|to submit|proposal cost)",
        r"^(\d+)\s+connects?$",
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

    apply_btn = await _one_consequential_control(
        page,
        '[data-test="apply-button"]:text-is("Apply Now"), '
        'button:text-is("Apply Now"), a:text-is("Apply Now")'
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
    toggles = await _enabled_elements(page, _DURATION_TOGGLE)
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
        selects = await _enabled_elements(page, _RATE_INCREASE_SELECT)
        toggles = await _enabled_elements(page, _RATE_INCREASE_TOGGLE)
    except Exception as error:
        details["message"] = f"Rate-increase control inspection failed: {type(error).__name__}."
        return {"status": "unavailable", "details": details}

    details["select_controls_seen"] = len(selects)
    details["toggle_controls_seen"] = len(toggles)
    if not selects and not toggles:
        details["message"] = "The exact hourly form exposes no readable rate-increase control."
        return {"status": "unavailable", "details": details}
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
    'button:has-text("Edit profile highlights"), '
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
_PROFILE_HIGHLIGHT_ACTION_BUTTONS = (
    f'{_PROFILE_HIGHLIGHT_SELECT_BUTTONS}, '
    'button:text-is("Selected"), '
    'button:text-is("Remove highlight"), '
    'button[data-test*="selected-highlight"], '
    '[data-test*="profile-highlight"] button[aria-pressed="true"]'
)
_PROFILE_HIGHLIGHT_CLOSE = (
    '[role="dialog"] button[aria-label="Close"], '
    '[role="dialog"] button[aria-label*="close" i], '
    '.is-modal-fullscreen button[aria-label="Close"], '
    '.is-modal-fullscreen button:has-text("Cancel"), '
    '[role="dialog"] button:has-text("Cancel")'
)
_REQUIRED_PROFILE_HIGHLIGHT_TABS = frozenset(
    {"portfolio", "certifications", "upwork_jobs"}
)


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


def _normalized_highlight_identity(value: str) -> str:
    """Normalize only presentation differences before exact title comparison."""

    return _normalize_visible_title(value).casefold()


async def _element_is_visible(element) -> bool:
    try:
        return bool(await element.is_visible())
    except Exception:
        # Detached or unreadable controls cannot contribute to complete live
        # evidence for an approval-bound consequential action.
        return False


async def _element_is_enabled(element) -> bool:
    try:
        return bool(await element.is_enabled())
    except Exception:
        # Detached or unreadable controls cannot be safely interacted with.
        return False


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
          && !/^(select highlight|selected|remove highlight|add to highlights|view details)$/i.test(text)
          && !/^add profile highlights$/i.test(text);
      };

      const ariaLabel = clean(button.getAttribute('aria-label'));
      const ariaMatch = ariaLabel.match(/^(?:select highlight|selected|remove highlight)(?: for)?\s*[:\-]?\s*(.+)$/i);
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
          /^(?:select highlight|selected|remove highlight)$/i.test(clean(candidate.innerText)) && visible(candidate)
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
    for button in await _enabled_elements(page, _PROFILE_HIGHLIGHT_SELECT_BUTTONS):
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
    for index, tab in enumerate(await _enabled_elements(page, _PROFILE_HIGHLIGHT_TABS)):
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
    close_button = await _one_enabled(page, _PROFILE_HIGHLIGHT_CLOSE)
    if close_button is not None:
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
    open_buttons = await _enabled_elements(page, _PROFILE_HIGHLIGHT_OPENER)
    result["details"]["opener_controls_seen"] = len(open_buttons)
    if len(open_buttons) != 1:
        result["details"]["message"] = (
            "One exact visible profile-highlight chooser control was not found."
        )
        return result
    open_button = open_buttons[0]

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

    base_connects_inspection = await _inspect_base_connects_state(page)
    boost_auction_inspection = await _inspect_boost_auction_state(page)
    fee_net_inspection: dict[str, Any] = {
        "text": [],
        "status": "unavailable",
        "details": {
            "evidence_scope": "exact_upwork_controls",
            "message": (
                "Fee/net evidence requires the reversible commercial preflight after the exact "
                "rate or by-project bid is entered."
            ),
        },
    }
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
            "text": [],
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
        base_connects_inspection = {
            "value": None,
            "status": "unavailable",
            "text": base_connects_inspection["text"],
            "details": {
                **base_connects_inspection["details"],
                "message": "The proposal form is not ready, so base-Connect cost is unavailable.",
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
        "base_connects": base_connects_inspection["value"],
        "base_connects_status": base_connects_inspection["status"],
        "base_connects_text": base_connects_inspection["text"],
        "base_connects_details": base_connects_inspection["details"],
        "fee_net_text": fee_net_inspection["text"],
        "fee_net_status": fee_net_inspection["status"],
        "fee_net_details": fee_net_inspection["details"],
        "fee_net_price_amount": None,
        "fee_net_source": None,
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


async def _enabled_elements(scope, selector: str) -> list[Any]:
    """Return only controls proven both visible and enabled.

    Upwork leaves hidden responsive and modal clones in the DOM.  Every
    interaction resolver uses this common fail-closed definition so an enabled
    hidden clone can never become an action target.
    """

    enabled: list[Any] = []
    try:
        candidates = await scope.query_selector_all(selector)
    except Exception:
        return enabled
    for element in candidates:
        if not await _element_is_visible(element):
            continue
        if await _element_is_enabled(element):
            enabled.append(element)
    return enabled


async def _one_enabled(scope, selector: str) -> Any | None:
    controls = await _enabled_elements(scope, selector)
    return controls[0] if len(controls) == 1 else None


async def _visible_enabled_elements(scope, selector: str) -> list[Any]:
    """Compatibility name for the shared visible-and-enabled resolver."""

    return await _enabled_elements(scope, selector)


async def _one_consequential_control(scope, selector: str) -> Any | None:
    """Resolve exactly one visible and enabled consequential control."""

    controls = await _visible_enabled_elements(scope, selector)
    return controls[0] if len(controls) == 1 else None


async def _fill_and_readback_text(element, value: str) -> bool:
    """Fill a consequential text field and require byte-for-byte readback."""

    try:
        await element.fill(value)
        return str(await element.input_value()) == value
    except Exception:
        return False


async def _selected_option_label(select) -> str | None:
    """Read the selected option's visible label, never merely its opaque value."""

    try:
        selected = await select.evaluate(
            "element => element.selectedOptions.length === 1 "
            "? element.selectedOptions[0].textContent : null"
        )
    except Exception:
        selected = None
    if isinstance(selected, str):
        return _normalise_identity_text(selected)
    try:
        selected_options = await select.query_selector_all("option:checked")
    except Exception:
        selected_options = []
    if len(selected_options) != 1:
        return None
    try:
        return _normalise_identity_text(await selected_options[0].text_content())
    except Exception:
        return None


async def _dropdown_selection_readback(page, toggle, approved_label: str) -> bool:
    """Require an exact visible label or one exact aria-selected option."""

    try:
        if _normalise_identity_text(await toggle.text_content()) == approved_label:
            return True
    except Exception:
        pass
    selected: list[Any] = []
    try:
        candidates = await page.query_selector_all(
            '[role="option"][aria-selected="true"], '
            '[role="menuitem"][aria-selected="true"], '
            'li.air3-menu-item[aria-selected="true"]'
        )
    except Exception:
        candidates = []
    for option in candidates:
        if await _element_is_visible(option):
            selected.append(option)
    if len(selected) != 1:
        return False
    try:
        return _normalise_identity_text(await selected[0].text_content()) == approved_label
    except Exception:
        return False


async def _select_exact_dropdown_label(page, toggle, approved_label: str) -> bool:
    try:
        await _click(page, toggle)
    except Exception:
        return False
    matches: list[Any] = []
    try:
        options = await _enabled_elements(
            page,
            'li.air3-menu-item, [role="option"], [role="menuitem"]'
        )
    except Exception:
        return False
    for option in options:
        try:
            label = _normalise_identity_text(await option.text_content())
        except Exception:
            continue
        if label == approved_label:
            matches.append(option)
    if len(matches) != 1:
        return False
    try:
        await _click(page, matches[0])
    except Exception:
        return False
    return await _dropdown_selection_readback(page, toggle, approved_label)


async def _select_duration(page, duration: str) -> bool:
    selects = await _visible_enabled_elements(
        page,
        'select[name*="duration"], [data-test*="duration"] select',
    )
    if selects:
        if len(selects) != 1:
            return False
        try:
            await selects[0].select_option(label=duration)
        except Exception:
            return False
        return await _selected_option_label(selects[0]) == duration

    toggle = await _one_consequential_control(
        page,
        'button:has-text("Select a duration"), '
        '.air3-dropdown-toggle:has-text("duration"), '
        '[data-test*="duration"] .air3-dropdown-toggle',
    )
    if toggle is None:
        return False
    return await _select_exact_dropdown_label(page, toggle, duration)


async def _select_rate_increase_never(
    page,
    approved_control_status: str | None = None,
) -> bool:
    """Select Never, or accept absence only when approval bound not_applicable."""

    if approved_control_status not in {None, "complete", "not_applicable"}:
        return False
    selects = await _visible_enabled_elements(
        page,
        'select[name*="increase"], [data-test*="rate-increase"] select',
    )
    toggles = await _visible_enabled_elements(
        page,
        '[data-test*="rate-increase"] button, '
        '.air3-dropdown-toggle:has-text("rate increase"), '
        'button:has-text("Select a frequency")',
    )
    if not selects and not toggles:
        return approved_control_status == "not_applicable"
    if approved_control_status == "not_applicable":
        return False
    if selects and toggles:
        return False
    if selects:
        if len(selects) != 1:
            return False
        try:
            await selects[0].select_option(label="Never")
        except Exception:
            return False
        return await _selected_option_label(selects[0]) == "Never"
    if len(toggles) != 1:
        return False
    return await _select_exact_dropdown_label(page, toggles[0], "Never")


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
        if not await _element_is_visible(candidate):
            continue
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
    matches = await _enabled_elements(
        section,
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
    element = await _one_consequential_control(page, selectors)
    if element is None:
        return False
    try:
        await element.fill(value)
        live_value = str(await element.input_value()).replace(",", "").replace("$", "").strip()
    except Exception:
        return False
    try:
        return Decimal(live_value) == Decimal(value)
    except Exception:
        return False


_HOURLY_RATE_INPUT_SELECTOR = (
    '[data-test="hourly-rate-input"], input[name="rate"], input[name="hourlyRate"]'
)
_BY_PROJECT_AMOUNT_INPUT_SELECTOR = (
    '[data-test="bid-input"], input[name="bid"], input[name="amount"], '
    'input[data-test="project-amount"], input[placeholder="$0.00"]'
)


async def _commercial_preflight_price_control(
    page,
    params: InspectProposalCommercialPreflightParams,
) -> tuple[Any | None, str | None]:
    """Resolve one reversible price input without changing payment structure."""

    if params.rate is not None:
        control = await _one_consequential_control(page, _HOURLY_RATE_INPUT_SELECTOR)
        return (
            (control, None)
            if control is not None
            else (None, "One exact hourly rate control was not available for preflight")
        )

    section = await _fixed_payment_section(page)
    if section is None:
        return None, "One exact fixed-price payment section was not available for preflight"
    project = await _exact_payment_radio(section, "By project")
    milestone = await _exact_payment_radio(section, "By milestone")
    if (
        project is None
        or milestone is None
        or await _checked_state(project) is not True
        or await _checked_state(milestone) is not False
    ):
        return (
            None,
            "By-project must already be selected; commercial preflight will not mutate payment structure",
        )
    control = await _one_consequential_control(section, _BY_PROJECT_AMOUNT_INPUT_SELECTOR)
    return (
        (control, None)
        if control is not None
        else (None, "One exact by-project amount control was not available for preflight")
    )


async def inspect_proposal_commercial_preflight(
    params: InspectProposalCommercialPreflightParams,
) -> dict[str, Any]:
    """Temporarily enter an exact price, read scoped fee/net controls, then restore it."""

    browser = get_browser()
    await browser.ensure_logged_in()
    async with browser.operation() as page:
        return await _inspect_proposal_commercial_preflight_on_page(params, page)


def _decimal_control_value(value: Any) -> Decimal | None:
    """Normalize a rendered price control value without guessing its currency."""

    normalized = str(value or "").replace(",", "").strip()
    normalized = re.sub(r"^[\s$£€]+", "", normalized).strip()
    try:
        amount = Decimal(normalized)
    except (ArithmeticError, ValueError):
        return None
    return amount if amount.is_finite() else None


def _fee_net_signature(snapshot: Mapping[str, Any]) -> tuple[str, tuple[str, ...]] | None:
    status = snapshot.get("status")
    text = snapshot.get("text")
    if status not in {"complete", "incomplete", "unavailable"}:
        return None
    if not isinstance(text, list) or not all(isinstance(value, str) for value in text):
        return None
    return str(status), tuple(_dedupe_text(text))


def _fee_net_gross_reconciliation(
    snapshot: Mapping[str, Any],
    approved_gross: Decimal,
) -> dict[str, Any]:
    """Prove exact same-currency fee + net equals the approved gross."""

    raw_details = snapshot.get("details")
    details = dict(raw_details) if isinstance(raw_details, Mapping) else {}
    try:
        fee_amount = Decimal(str(details["fee_amount"]))
        net_amount = Decimal(str(details["net_amount"]))
    except (ArithmeticError, KeyError, ValueError):
        fee_amount = None
        net_amount = None
    fee_currency = details.get("fee_currency")
    net_currency = details.get("net_currency")
    amounts_unambiguous = bool(
        details.get("amounts_unambiguous") is True
        and fee_amount is not None
        and net_amount is not None
    )
    same_currency = bool(
        details.get("same_currency") is True
        and isinstance(fee_currency, str)
        and fee_currency
        and fee_currency == net_currency
    )
    total = (
        (fee_amount + net_amount).quantize(Decimal("0.01"))
        if amounts_unambiguous and fee_amount is not None and net_amount is not None
        else None
    )
    approved = approved_gross.quantize(Decimal("0.01"))
    return {
        "amounts_unambiguous": amounts_unambiguous,
        "same_currency": same_currency,
        "currency": fee_currency if same_currency else None,
        "fee_amount": format(fee_amount, ".2f") if fee_amount is not None else None,
        "net_amount": format(net_amount, ".2f") if net_amount is not None else None,
        "fee_plus_net": format(total, ".2f") if total is not None else None,
        "approved_gross": format(approved, ".2f"),
        "gross_matches": bool(amounts_unambiguous and same_currency and total == approved),
    }


async def _stable_scoped_fee_net_snapshot(page) -> tuple[dict[str, Any], bool]:
    """Require two identical scoped fee/net reads separated by a short settle."""

    try:
        first = await _inspect_fee_net_state(page)
        try:
            await page.wait_for_timeout(150)
        except Exception:
            pass
        second = await _inspect_fee_net_state(page)
    except Exception:
        return (
            {
                "text": [],
                "status": "unavailable",
                "details": {"message": "Scoped fee/net controls could not be read twice."},
            },
            False,
        )
    first_signature = _fee_net_signature(first)
    second_signature = _fee_net_signature(second)
    stable = first_signature is not None and first_signature == second_signature
    result = dict(second)
    raw_details = second.get("details")
    details: dict[str, Any] = dict(raw_details) if isinstance(raw_details, Mapping) else {}
    result["details"] = {
        **details,
        "stable_scoped_readback": stable,
    }
    return result, stable


async def _inspect_proposal_commercial_preflight_on_page(
    params: InspectProposalCommercialPreflightParams,
    page,
) -> dict[str, Any]:
    """Run a fail-closed reversible commercial preflight under one browser lease."""

    _, expected_job_id, _ = parse_job_or_application_url(params.job_url)
    canonical_job_url, _ = parse_job_url(f"https://www.upwork.com/jobs/{expected_job_id}")
    form_status, existing_evidence = await _open_proposal_form(page, params.job_url)
    identity = await _application_identity_from_current_page(page) if form_status == "ready" else None
    expected_type = "hourly" if params.rate is not None else "fixed"
    base_result: dict[str, Any] = {
        "job_url": canonical_job_url,
        "job_id": expected_job_id,
        "form_url": identity.get("form_url") if identity else None,
        "job_title": identity.get("job_title") if identity else None,
        "job_type": identity.get("job_type") if identity else None,
        "form_status": form_status,
        "existing_proposal": existing_evidence is not None,
        "fee_net_text": [],
        "fee_net_status": "unavailable",
        "fee_net_price_amount": None,
        "fee_net_source": None,
        "price_restored": False,
        "identity_restored": False,
        "reversible_form_interaction": False,
        "external_action_taken": False,
    }
    if (
        identity is None
        or identity.get("job_id") != expected_job_id
        or identity.get("job_type") != expected_type
    ):
        base_result["fee_net_details"] = {
            "message": "The exact application identity and proposed price type could not be bound."
        }
        return base_result

    control, control_error = await _commercial_preflight_price_control(page, params)
    if control is None:
        base_result["fee_net_details"] = {"message": control_error}
        return base_result

    approved_amount = Decimal(str(params.rate if params.rate is not None else params.bid)).quantize(
        Decimal("0.01")
    )
    try:
        original_value = str(await control.input_value())
    except Exception:
        base_result["fee_net_details"] = {
            "message": "The original commercial price could not be read before reversible preflight."
        }
        return base_result
    original_amount = _decimal_control_value(original_value)
    if original_amount is None:
        base_result["fee_net_details"] = {
            "message": "The original commercial price could not be normalized before preflight."
        }
        return base_result
    original_fee_net, original_snapshot_stable = await _stable_scoped_fee_net_snapshot(page)
    if not original_snapshot_stable:
        base_result["fee_net_details"] = {
            "message": "The original scoped fee/net state was not stable enough for exact restoration."
        }
        return base_result

    inspection: dict[str, Any] = {
        "text": [],
        "status": "unavailable",
        "details": {"message": "Commercial preflight did not complete."},
    }
    exact_price_entered = False
    approved_snapshot_stable = False
    approved_price_stable = False
    stale_price_evidence = False
    restored = False
    identity_restored = False
    try:
        base_result["reversible_form_interaction"] = True
        await control.fill(format(approved_amount, "f"))
        live_value = _decimal_control_value(await control.input_value())
        exact_price_entered = live_value == approved_amount
        if not exact_price_entered:
            inspection["details"]["message"] = (
                "The approved commercial price could not be read back exactly."
            )
        else:
            blurred = False
            try:
                await control.press("Tab")
                blurred = True
            except Exception:
                inspection["details"]["message"] = (
                    "The approved price could not be blurred before fee/net readback."
                )
            if blurred:
                try:
                    await page.wait_for_timeout(300)
                except Exception:
                    pass
                inspection, approved_snapshot_stable = await _stable_scoped_fee_net_snapshot(page)
                approved_price_stable = (
                    _decimal_control_value(await control.input_value()) == approved_amount
                )
                stale_price_evidence = bool(
                    original_amount != approved_amount
                    and _fee_net_signature(inspection) == _fee_net_signature(original_fee_net)
                )
                gross_reconciliation = _fee_net_gross_reconciliation(
                    inspection,
                    approved_amount,
                )
                inspection.setdefault("details", {})["gross_reconciliation"] = (
                    gross_reconciliation
                )
                if not approved_snapshot_stable:
                    inspection["status"] = "incomplete"
                    inspection.setdefault("details", {})["message"] = (
                        "Scoped fee/net evidence changed between post-blur readbacks."
                    )
                elif not approved_price_stable:
                    inspection["status"] = "incomplete"
                    inspection.setdefault("details", {})["message"] = (
                        "The approved price changed while fee/net evidence was read."
                    )
                elif gross_reconciliation["gross_matches"] is not True:
                    inspection["status"] = "incomplete"
                    inspection.setdefault("details", {})["message"] = (
                        "The exact scoped fee and net amounts did not reconcile to the "
                        "approved same-currency gross price."
                    )
                elif stale_price_evidence:
                    inspection["status"] = "incomplete"
                    inspection.setdefault("details", {})["message"] = (
                        "Scoped fee/net evidence did not change from the different original price."
                    )
    except Exception as error:
        inspection["details"]["message"] = (
            f"Commercial preflight failed before fee/net readback: {type(error).__name__}."
        )
    finally:
        try:
            await control.fill(original_value)
            restored_blurred = False
            try:
                await control.press("Tab")
                restored_blurred = True
            except Exception:
                pass
            try:
                await page.wait_for_timeout(300)
            except Exception:
                pass
            restored_value_before = str(await control.input_value())
            restored_fee_net, restored_snapshot_stable = (
                await _stable_scoped_fee_net_snapshot(page)
            )
            restored_value_after = str(await control.input_value())
            restored_identity = await _application_identity_from_current_page(page)
            identity_restored = restored_identity == identity
            restored = bool(
                restored_value_before == original_value
                and restored_value_after == original_value
                and restored_blurred
                and identity_restored
                and restored_snapshot_stable
                and _fee_net_signature(restored_fee_net)
                == _fee_net_signature(original_fee_net)
            )
        except Exception:
            restored = False

    status = inspection.get("status")
    if not restored:
        status = "incomplete"
        inspection.setdefault("details", {})["message"] = (
            "Fee/net evidence was discarded because the original price could not be restored exactly."
        )
    elif not exact_price_entered:
        status = "incomplete"
    base_result.update(
        {
            "fee_net_text": inspection.get("text") or [],
            "fee_net_status": status,
            "fee_net_details": {
                **(inspection.get("details") or {}),
                "approved_price_entered": exact_price_entered,
                "approved_price_stable_during_readback": approved_price_stable,
                "stable_post_blur_scoped_readback": approved_snapshot_stable,
                "stale_original_price_evidence_rejected": stale_price_evidence,
                "price_restored": restored,
            },
            "fee_net_price_amount": format(approved_amount, ".2f")
            if status == "complete" and restored and exact_price_entered
            else None,
            "fee_net_source": "scoped_reversible_price_preflight"
            if status == "complete" and restored and exact_price_entered
            else None,
            "price_restored": restored,
            "identity_restored": identity_restored,
            "external_action_taken": bool(
                base_result["reversible_form_interaction"] and not restored
            ),
        }
    )
    return base_result


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
            _BY_PROJECT_AMOUNT_INPUT_SELECTOR,
            str(params.bid),
        )
        return (True, None) if ok else (False, "One exact by-project total input could not be filled and verified")

    rows = await section.query_selector_all(
        '[data-test="milestone-row"], [data-test*="milestone-item"], .milestone-row'
    )
    if len(rows) != len(params.milestones):
        return False, "Live milestone rows differ from the exact approved milestones"
    for row, milestone in zip(rows, params.milestones, strict=True):
        description = await _one_consequential_control(
            row,
            'input[name*="description"], textarea[name*="description"], [data-test*="description"] input'
        )
        due_date = await _one_consequential_control(
            row,
            'input[name*="due"], input[name*="date"], [data-test*="due-date"] input'
        )
        amount = await _one_consequential_control(
            row,
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


    async def exact_dialog() -> Any | None:
        matching_dialogs: list[Any] = []
        try:
            dialogs = await page.query_selector_all('[role="dialog"]')
        except Exception:
            return None
        for candidate in dialogs:
            if not await _element_is_visible(candidate):
                continue
            try:
                dialog_text = _normalise_identity_text(await candidate.text_content())
            except Exception:
                continue
            if re.search(r"\b3 things you need to know\b", dialog_text, re.I) and re.search(
                r"\bYes, I understand\.?\b", dialog_text, re.I
            ):
                matching_dialogs.append(candidate)
        return matching_dialogs[0] if len(matching_dialogs) == 1 else None

    dialog = await exact_dialog()
    if dialog is None:
        return False
    acknowledgement_selector = (
        'label:text-is("Yes, I understand") input[type="checkbox"], '
        'label:text-is("Yes, I understand.") input[type="checkbox"], '
        'input[type="checkbox"][aria-label="Yes, I understand"], '
        'input[type="checkbox"][aria-label="Yes, I understand."]'
    )
    acknowledgements = await _enabled_elements(dialog, acknowledgement_selector)
    continue_buttons = await _enabled_elements(dialog, 'button:text-is("Continue")')
    if len(acknowledgements) != 1 or len(continue_buttons) != 1:
        return False
    acknowledgement = acknowledgements[0]
    try:
        await acknowledgement.check()
    except Exception:
        return False
    if await _checked_state(acknowledgement) is not True:
        return False

    # Re-resolve the one visible dialog and its exact controls after checking.
    # The final Continue is a direct actionability-checked click: there is no
    # DOM fallback capable of reaching a hidden warning control.
    dialog = await exact_dialog()
    if dialog is None:
        return False
    acknowledgements = await _enabled_elements(dialog, acknowledgement_selector)
    continue_buttons = await _enabled_elements(dialog, 'button:text-is("Continue")')
    if (
        len(acknowledgements) != 1
        or len(continue_buttons) != 1
        or await _checked_state(acknowledgements[0]) is not True
    ):
        return False
    try:
        await continue_buttons[0].click()
    except Exception:
        return False
    return True


async def _highlight_action_selected(button) -> bool | None:
    """Read a chooser card's explicit selected/unselected state."""

    try:
        label = _normalise_identity_text(await button.text_content()).casefold()
    except Exception:
        label = ""
    if label in {"select", "select highlight"}:
        return False
    if label in {"selected", "remove highlight"}:
        return True
    for attribute in ("aria-pressed", "aria-checked", "data-selected"):
        try:
            value = str(await button.get_attribute(attribute) or "").casefold()
        except Exception:
            continue
        if value == "true":
            return True
        if value == "false":
            return False
    return None


async def _exact_profile_highlight_chooser(page) -> Any | None:
    try:
        candidates = await page.query_selector_all(_PROFILE_HIGHLIGHT_CHOOSER)
    except Exception:
        return None
    visible = [candidate for candidate in candidates if await _element_is_visible(candidate)]
    return visible[0] if len(visible) == 1 else None


async def _open_profile_highlight_chooser(page) -> Any | None:
    openers = await _enabled_elements(page, _PROFILE_HIGHLIGHT_OPENER)
    if len(openers) != 1:
        return None
    try:
        await _click(page, openers[0])
        await _wait_for_profile_highlight_chooser(page)
    except Exception:
        return None
    return await _exact_profile_highlight_chooser(page)


async def _activate_profile_highlight_tab(page, identity: str) -> bool:
    if identity == "current_view":
        return not bool(await _profile_highlight_tabs(page))
    matches = [tab for tab_identity, tab in await _profile_highlight_tabs(page) if tab_identity == identity]
    if len(matches) != 1:
        return False
    try:
        await _click(page, matches[0])
        await _settle_profile_highlight_view(page)
    except Exception:
        return False
    return True


async def _visible_profile_highlight_records(page, tab_identity: str) -> tuple[list[dict[str, Any]], str | None]:
    records: list[dict[str, Any]] = []
    try:
        buttons = await _enabled_elements(page, _PROFILE_HIGHLIGHT_ACTION_BUTTONS)
    except Exception:
        return [], "Profile highlight controls could not be enumerated"
    for button in buttons:
        title = await _highlight_title_for_button(button)
        selected = await _highlight_action_selected(button)
        if not title or selected is None:
            return [], "A live profile highlight title or selected state could not be read"
        records.append(
            {
                "title": title,
                "identity": _normalized_highlight_identity(title),
                "selected": selected,
                "tab": tab_identity,
                "button": button,
            }
        )
    return records, None


async def _enumerate_profile_highlight_records(
    page,
) -> tuple[list[dict[str, Any]], str | None]:
    tabs = await _profile_highlight_tabs(page)
    tab_identities = {identity for identity, _ in tabs}
    if not _REQUIRED_PROFILE_HIGHLIGHT_TABS.issubset(tab_identities):
        return [], "The complete required profile-highlight tab set is not visible"
    views = [identity for identity, _ in tabs] or ["current_view"]
    records: list[dict[str, Any]] = []
    for identity in views:
        if not await _activate_profile_highlight_tab(page, identity):
            return [], f"Profile highlight tab could not be inspected: {identity}"
        visible, error = await _visible_profile_highlight_records(page, identity)
        if error:
            return [], error
        records.extend(visible)
    return records, None


def _index_profile_highlight_records(
    records: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    indexed: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        indexed.setdefault(str(record["identity"]), []).append(record)
    return indexed


async def _select_profile_highlights(page, highlights: list[str]) -> tuple[bool, str | None]:
    approved_identities = [_normalized_highlight_identity(title) for title in highlights]
    if not all(approved_identities) or len(set(approved_identities)) != len(approved_identities):
        return False, "Approved profile highlight titles are blank or ambiguous after normalization"

    chooser = await _open_profile_highlight_chooser(page)
    if chooser is None:
        if not highlights and not await _enabled_elements(page, _PROFILE_HIGHLIGHT_OPENER):
            return True, None
        return False, "One exact profile highlights chooser could not be opened"

    records, error = await _enumerate_profile_highlight_records(page)
    if error:
        await _dismiss_profile_highlight_chooser(page)
        return False, error
    indexed = _index_profile_highlight_records(records)
    for identity in approved_identities:
        if len(indexed.get(identity, [])) != 1:
            await _dismiss_profile_highlight_chooser(page)
            return False, "An approved profile highlight did not match one exact live title"
    selected_before = {str(record["identity"]) for record in records if record["selected"]}
    if not selected_before.issubset(set(approved_identities)):
        await _dismiss_profile_highlight_chooser(page)
        return False, "The live form contains an unapproved selected profile highlight"

    changed = False
    for identity in approved_identities:
        current = indexed[identity][0]
        if current["selected"]:
            continue
        if not await _activate_profile_highlight_tab(page, str(current["tab"])):
            await _dismiss_profile_highlight_chooser(page)
            return False, "An approved profile highlight tab could not be reopened"
        visible, visible_error = await _visible_profile_highlight_records(page, str(current["tab"]))
        if visible_error:
            await _dismiss_profile_highlight_chooser(page)
            return False, visible_error
        matches = [record for record in visible if record["identity"] == identity]
        if len(matches) != 1 or matches[0]["selected"]:
            await _dismiss_profile_highlight_chooser(page)
            return False, "An approved profile highlight changed during selection"
        try:
            await _click(page, matches[0]["button"])
            await _settle_profile_highlight_view(page)
        except Exception:
            await _dismiss_profile_highlight_chooser(page)
            return False, "An approved profile highlight could not be selected"
        visible, visible_error = await _visible_profile_highlight_records(page, str(current["tab"]))
        selected_matches = [
            record
            for record in visible
            if record["identity"] == identity and record["selected"]
        ]
        if visible_error or len(selected_matches) != 1:
            await _dismiss_profile_highlight_chooser(page)
            return False, "An approved profile highlight selection could not be read back"
        changed = True

    records, error = await _enumerate_profile_highlight_records(page)
    selected = {str(record["identity"]) for record in records if record["selected"]}
    if error or selected != set(approved_identities):
        await _dismiss_profile_highlight_chooser(page)
        return False, "The live selected profile highlight set differs from approval"

    if changed:
        add_buttons = await _enabled_elements(chooser, 'button:text-is("Add to highlights")')
        if len(add_buttons) != 1:
            await _dismiss_profile_highlight_chooser(page)
            return False, "One exact Add to highlights control was not found"
        try:
            await _click(page, add_buttons[0])
            await _settle_profile_highlight_view(page)
        except Exception:
            return False, "The approved profile highlight set could not be saved"
        if await _profile_highlight_chooser_visible(page):
            await _dismiss_profile_highlight_chooser(page)
            return False, "The profile highlight chooser did not confirm the saved set"

        chooser = await _open_profile_highlight_chooser(page)
        if chooser is None:
            return False, "The saved profile highlight set could not be reopened for readback"
        records, error = await _enumerate_profile_highlight_records(page)
        selected = {str(record["identity"]) for record in records if record["selected"]}
        if error or selected != set(approved_identities):
            await _dismiss_profile_highlight_chooser(page)
            return False, "The saved profile highlight set differs from approval"

    if not await _dismiss_profile_highlight_chooser(page):
        return False, "The verified profile highlight chooser could not be dismissed"
    return True, None


async def _readback_profile_highlights(
    page,
    highlights: list[str],
) -> tuple[bool, str | None]:
    """Re-read the exact selected set without selecting or saving anything."""

    approved = {_normalized_highlight_identity(value) for value in highlights}
    chooser = await _open_profile_highlight_chooser(page)
    if chooser is None:
        no_openers = not await _enabled_elements(page, _PROFILE_HIGHLIGHT_OPENER)
        return (
            (True, None)
            if not approved and no_openers
            else (False, "The approved profile highlights could not be reopened for final readback")
        )
    records, error = await _enumerate_profile_highlight_records(page)
    selected = {str(record["identity"]) for record in records if record["selected"]}
    dismissed = await _dismiss_profile_highlight_chooser(page)
    if error:
        return False, error
    if not dismissed:
        return False, "The profile-highlight chooser could not be dismissed after final readback"
    if selected != approved:
        return False, "The selected profile highlights silently changed after their earlier readback"
    return True, None


async def _readback_decimal_input(scope, selector: str, expected: Decimal) -> bool:
    control = await _one_consequential_control(scope, selector)
    if control is None:
        return False
    try:
        value = str(await control.input_value()).replace(",", "").replace("$", "").strip()
        return Decimal(value) == expected
    except Exception:
        return False


async def _readback_text_input(scope, selector: str, expected: str) -> bool:
    control = await _one_consequential_control(scope, selector)
    if control is None:
        return False
    try:
        return str(await control.input_value()) == expected
    except Exception:
        return False


async def _readback_duration(page, expected: str) -> bool:
    selects = await _visible_enabled_elements(
        page,
        'select[name*="duration"], [data-test*="duration"] select',
    )
    toggles = await _visible_enabled_elements(
        page,
        'button:has-text("Select a duration"), '
        '.air3-dropdown-toggle:has-text("duration"), '
        '[data-test*="duration"] .air3-dropdown-toggle',
    )
    if len(selects) == 1 and not toggles:
        return await _selected_option_label(selects[0]) == expected
    if len(toggles) == 1 and not selects:
        try:
            return _normalise_identity_text(await toggles[0].text_content()) == expected
        except Exception:
            return False
    return False


async def _readback_rate_increase(
    page,
    *,
    job_type: Literal["hourly", "fixed"],
    approved_status: RateIncreaseControlStatus,
) -> bool:
    selects = await _visible_enabled_elements(page, _RATE_INCREASE_SELECT)
    toggles = await _visible_enabled_elements(page, _RATE_INCREASE_TOGGLE)
    if job_type == "fixed":
        return approved_status == "not_applicable" and not selects and not toggles
    if approved_status != "complete":
        return False
    if len(selects) == 1 and not toggles:
        return await _selected_option_label(selects[0]) == "Never"
    if len(toggles) == 1 and not selects:
        try:
            return _normalise_identity_text(await toggles[0].text_content()) == "Never"
        except Exception:
            return False
    return False


async def _readback_fixed_payment_terms(page, params: SubmitProposalParams) -> bool:
    if params.job_type != "fixed" or params.bid is None or params.payment_structure is None:
        return params.job_type != "fixed"
    section = await _fixed_payment_section(page)
    if section is None:
        return False
    selected_label = "By project" if params.payment_structure == "by_project" else "By milestone"
    opposite_label = "By milestone" if params.payment_structure == "by_project" else "By project"
    selected = await _exact_payment_radio(section, selected_label)
    opposite = await _exact_payment_radio(section, opposite_label)
    if (
        selected is None
        or opposite is None
        or await _checked_state(selected) is not True
        or await _checked_state(opposite) is not False
    ):
        return False
    if params.payment_structure == "by_project":
        return await _readback_decimal_input(
            section,
            _BY_PROJECT_AMOUNT_INPUT_SELECTOR,
            Decimal(str(params.bid)),
        )

    try:
        rows = await section.query_selector_all(
            '[data-test="milestone-row"], [data-test*="milestone-item"], .milestone-row'
        )
    except Exception:
        return False
    if len(rows) != len(params.milestones):
        return False
    for row, milestone in zip(rows, params.milestones, strict=True):
        try:
            description = await _one_consequential_control(
                row,
                'input[name*="description"], textarea[name*="description"], '
                '[data-test*="description"] input'
            )
            due_date = await _one_consequential_control(
                row,
                'input[name*="due"], input[name*="date"], [data-test*="due-date"] input'
            )
            amount = await _one_consequential_control(
                row,
                'input[name*="amount"], input[data-test*="amount"]'
            )
            if not description or not due_date or not amount:
                return False
            live_description = _normalise_identity_text(await description.input_value())
            live_due_date = str(await due_date.input_value()).strip()
            live_amount = Decimal(
                str(await amount.input_value()).replace(",", "").replace("$", "").strip()
            )
        except Exception:
            return False
        if (
            live_description != milestone.description
            or live_due_date != milestone.due_date
            or live_amount != Decimal(str(milestone.amount))
        ):
            return False
    return True


async def _reinspect_approved_commercial_state(
    page,
    params: SubmitProposalParams,
) -> tuple[bool, str | None]:
    """Exact-compare the approval-bound fee and boost auction snapshots."""

    checks = (
        ("fee_net", _inspect_fee_net_state),
        ("boost_auction", _inspect_boost_auction_state),
    )
    for prefix, inspector in checks:
        expected_text = getattr(params, f"{prefix}_text", None)
        expected_status = getattr(params, f"{prefix}_status", None)
        if not isinstance(expected_text, list) or not all(
            isinstance(value, str) for value in expected_text
        ):
            return False, f"Approved {prefix} text was not bound"
        if expected_status not in {"complete", "incomplete", "unavailable"}:
            return False, f"Approved {prefix} status was not bound"
        if not callable(inspector):
            return False, f"Live {prefix} inspector is unavailable"
        try:
            live = await inspector(page)
        except Exception:
            return False, f"Live {prefix} state could not be read"
        live_text = live.get("text") if isinstance(live, Mapping) else None
        live_status = live.get("status") if isinstance(live, Mapping) else None
        if not isinstance(live_text, list) or not all(
            isinstance(value, str) for value in live_text
        ):
            return False, f"Live {prefix} text could not be read"
        if (
            _dedupe_text(expected_text) != _dedupe_text(live_text)
            or live_status != expected_status
        ):
            return False, f"Live {prefix} state changed after approval"
    return True, None


async def _reinspect_every_approved_live_state(
    page,
    params: SubmitProposalParams,
    approved_identity: Mapping[str, Any],
) -> tuple[bool, str | None]:
    """Final non-submit query pass after every form interaction and before Submit lookup."""

    highlights_ok, highlights_error = await _readback_profile_highlights(
        page,
        params.profile_highlights,
    )
    if not highlights_ok:
        return False, highlights_error

    commercial_ok, commercial_error = await _reinspect_approved_commercial_state(page, params)
    if not commercial_ok:
        return False, commercial_error

    if await _application_identity_from_current_page(page) != dict(approved_identity):
        return False, "The exact application identity silently changed before submission"

    base = await _inspect_base_connects_state(page)
    if base.get("status") != "complete" or base.get("value") != params.base_connects:
        return False, "The exact scoped base-Connect cost silently changed before submission"

    if await _screening_question_texts(page) != params.screening_questions:
        return False, "The screening questions silently changed before submission"

    if params.rate is not None and not await _readback_decimal_input(
        page,
        _HOURLY_RATE_INPUT_SELECTOR,
        Decimal(str(params.rate)),
    ):
        return False, "The approved hourly rate silently changed before submission"
    if not await _readback_fixed_payment_terms(page, params):
        return False, "The approved fixed-price terms silently changed before submission"

    if not await _readback_text_input(
        page,
        'textarea[data-test="cover-letter-input"], '
        '[data-test="cover-letter-input"] textarea, textarea[name="coverLetter"]',
        params.cover_letter,
    ):
        return False, "The approved cover letter silently changed before submission"

    answers = params.answers or []
    try:
        answer_controls = await _visible_enabled_elements(page, _SCREENING_ANSWER_CONTROLS)
    except Exception:
        return False, "The screening answer controls could not be re-read before submission"
    if len(answer_controls) != len(answers):
        return False, "The screening answer controls silently changed before submission"
    for control, answer in zip(answer_controls, answers, strict=True):
        try:
            if str(await control.input_value()) != answer:
                return False, "An approved screening answer silently changed before submission"
        except Exception:
            return False, "An approved screening answer could not be re-read before submission"

    if params.duration is None or not await _readback_duration(page, params.duration):
        return False, "The approved duration silently changed before submission"
    if not await _readback_rate_increase(
        page,
        job_type=params.job_type,
        approved_status=params.rate_increase_control_status,
    ):
        return False, "The approved rate-increase state silently changed before submission"
    return True, None


async def _first_stage_submit_control(page) -> Any | None:
    """Resolve one exact first-stage control only after every form readback."""

    return await _one_consequential_control(
        page,
        'button[data-test="submit-proposal"]:text-is("Submit proposal"), '
        '[data-test="submit-proposal"] button:text-is("Submit proposal")',
    )


async def _exact_boost_dialog(page) -> Any | None:
    try:
        dialogs = await page.query_selector_all('[role="dialog"]')
    except Exception:
        return None
    matches: list[Any] = []
    for dialog in dialogs:
        if not await _element_is_visible(dialog):
            continue
        try:
            text = _normalise_identity_text(await dialog.text_content())
        except Exception:
            continue
        if re.search(r"\bboost\b.*\bproposal\b|\bproposal\b.*\bboost\b", text, re.I) and re.search(
            r"\bconnects?\b",
            text,
            re.I,
        ):
            matches.append(dialog)
    return matches[0] if len(matches) == 1 else None


async def _explicit_selection_state(element) -> bool | None:
    try:
        return bool(await element.is_checked())
    except Exception:
        pass
    for attribute in ("aria-checked", "aria-pressed", "data-selected"):
        try:
            value = str(await element.get_attribute(attribute) or "").casefold()
        except Exception:
            continue
        if value == "true":
            return True
        if value == "false":
            return False
    return None


async def _exact_final_send_control(dialog, approved_base_connects: int) -> Any | None:
    """Resolve only a Send label whose cost exactly matches approved base Connects."""

    candidates = await _visible_enabled_elements(dialog, "button")
    matches: list[Any] = []
    for button in candidates:
        try:
            label = _normalise_identity_text(await button.text_content())
        except Exception:
            continue
        match = re.fullmatch(r"Send for ([0-9]+) Connects?", label, re.I)
        if match and int(match.group(1)) == approved_base_connects:
            matches.append(button)
    return matches[0] if len(matches) == 1 else None


async def _configure_boost_step(
    page,
    boost_connects: int,
    approved_base_connects: int,
) -> tuple[Any | None, str | None]:
    """Read back boost/no-boost inside one exact dialog before finding Send."""

    if boost_connects > 0:
        return (
            None,
            "Automatic positive boost submission is disabled before any boost-dialog interaction",
        )

    dialog = await _exact_boost_dialog(page)
    if dialog is None:
        return None, "One exact boost proposal dialog was not found"
    choices = await _enabled_elements(
        dialog,
        'label:text-is("Don\'t boost") input[type="radio"], '
        'input[type="radio"][aria-label="Don\'t boost"], '
        'label:text-is("No, thanks") input[type="radio"], '
        'input[type="radio"][aria-label="No, thanks"], '
        'button:text-is("Don\'t boost"), button:text-is("No, thanks")',
    )
    if len(choices) != 1:
        return None, "One exact no-boost control was not found"
    selected = await _explicit_selection_state(choices[0])
    if selected is None:
        return None, "The no-boost control selected state could not be read"
    if not selected:
        try:
            await _click(page, choices[0])
        except Exception:
            return None, "The no-boost control could not be selected"
    if await _explicit_selection_state(choices[0]) is not True:
        return None, "The no-boost selection could not be read back"

    send = await _exact_final_send_control(dialog, approved_base_connects)
    if send is None:
        return (
            None,
            "One exact final Send control matching approved base Connects was not found",
        )
    return send, None


def _confirmed_submission_result(
    *,
    params: SubmitProposalParams,
    readback: Mapping[str, Any],
) -> dict[str, Any]:
    """Never report actual Connect spend unless the stored owner readback proves it."""

    result: dict[str, Any] = {
        "status": "submitted",
        "approved_base_connects": params.base_connects,
        "approved_boost_connects": params.boost_connects,
        "connects_spend_verified": False,
        "boost_spend_verified": False,
        "message": (
            "Proposal submission was read back from Upwork; actual Connect spend was not "
            "available in the stored proposal readback."
        ),
        "owner_system_readback": dict(readback),
        "external_action_taken": True,
    }
    if readback.get("connects_spend_verified") is True:
        connects_used = readback.get("connects_used")
        if (
            isinstance(connects_used, int)
            and not isinstance(connects_used, bool)
            and connects_used >= 0
        ):
            result["connects_used"] = connects_used
            result["connects_spend_verified"] = True
            result["message"] = "Proposal submission and actual Connect spend were read back from Upwork."
    return result


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

    Returns submission status, the approved cost, and actual Connect spend only when verified.
    """
    if params.boost_connects > 0:
        return {
            "status": "unsupported",
            "message": (
                "Automatic positive boost submission is disabled until the live Upwork flow "
                "can prove the first Submit click is non-consequential."
            ),
            "external_action_taken": False,
        }
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
    assert params.base_connects is not None

    if params.boost_connects > 0:
        return {
            "status": "unsupported",
            "message": (
                "Automatic positive boost submission is disabled before any application-form "
                "or Submit interaction."
            ),
            "external_action_taken": False,
        }

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

    live_base_connects_state = await _inspect_base_connects_state(page)
    live_base_connects = live_base_connects_state.get("value")
    if (
        live_base_connects_state.get("status") != "complete"
        or live_base_connects != params.base_connects
    ):
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
        rate_ok = await _fill_one_exact_input(
            page,
            _HOURLY_RATE_INPUT_SELECTOR,
            str(params.rate),
        )
        if not rate_ok:
            return {
                "status": "live_form_mismatch",
                "message": "One exact hourly rate could not be filled and read back.",
                "external_action_taken": False,
            }

    if params.bid is not None:
        fixed_ok, fixed_error = await _configure_fixed_payment_terms(page, params)
        if not fixed_ok:
            return {"status": "error", "message": fixed_error, "external_action_taken": False}

    cover_textarea = await _one_consequential_control(
        page,
        'textarea[data-test="cover-letter-input"], '
        '[data-test="cover-letter-input"] textarea, textarea[name="coverLetter"]',
    )
    if cover_textarea is None or not await _fill_and_readback_text(
        cover_textarea,
        params.cover_letter,
    ):
        return {
            "status": "live_form_mismatch",
            "message": "One exact cover letter could not be filled and read back.",
            "external_action_taken": False,
        }

    # Answer screening questions only when the live field count still matches.
    answers = params.answers or []
    question_inputs = await _visible_enabled_elements(page, _SCREENING_ANSWER_CONTROLS)
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
            if not await _fill_and_readback_text(question_inputs[i], answer):
                return {
                    "status": "live_form_mismatch",
                    "message": "An approved screening answer could not be read back exactly.",
                    "answer_index": i,
                    "external_action_taken": False,
                }

    if not await _select_duration(page, params.duration):
        return {"status": "error", "message": "Approved duration option could not be selected", "external_action_taken": False}
    if not await _select_rate_increase_never(
        page,
        params.rate_increase_control_status,
    ):
        return {
            "status": "live_form_mismatch",
            "message": 'Rate increase frequency/status could not be verified as "Never".',
            "external_action_taken": False,
        }
    if params.profile_highlights and params.available_profile_highlights_status != "complete":
        return {
            "status": "live_form_mismatch",
            "message": "Approved profile-highlight enumeration was not complete.",
            "external_action_taken": False,
        }
    highlights_ok, highlights_error = await _select_profile_highlights(page, params.profile_highlights)
    if not highlights_ok:
        return {
            "status": "live_form_mismatch",
            "message": highlights_error,
            "external_action_taken": False,
        }

    final_ok, final_error = await _reinspect_every_approved_live_state(
        page,
        params,
        approved_identity,
    )
    if not final_ok:
        return {
            "status": "live_form_mismatch",
            "message": final_error,
            "external_action_taken": False,
        }

    # This is intentionally the first query for a first-stage submit control.
    submit_btn = await _first_stage_submit_control(page)
    if submit_btn is None:
        return {"status": "error", "message": "Submit proposal button not found", "external_action_taken": False}

    await _click(page, submit_btn)
    consequential_click_taken = True

    immediate_confirmation = await _proposal_confirmation(
        page,
        approved_proposal_target,
        timeout_seconds=2,
    )
    if immediate_confirmation["confirmed"]:
        if params.boost_connects > 0:
            return {
                "status": "unknown",
                "message": (
                    "Upwork stored the proposal before the approved boost step could be verified; "
                    "do not retry automatically and do not treat the boost bid as spent."
                ),
                "owner_system_readback": immediate_confirmation,
                "boost_spend_verified": False,
                "external_action_taken": True,
            }
        return _confirmed_submission_result(params=params, readback=immediate_confirmation)

    send_btn, boost_error = await _configure_boost_step(
        page,
        params.boost_connects,
        params.base_connects,
    )
    if send_btn is None:
        return {
            "status": "unknown",
            "message": boost_error,
            "boost_spend_verified": False,
            "external_action_taken": consequential_click_taken,
        }
    await _click(page, send_btn)

    if params.bid is not None:
        await _acknowledge_fixed_price_warning(page)

    readback = await _proposal_confirmation(
        page,
        approved_proposal_target,
    )
    if readback["confirmed"]:
        return _confirmed_submission_result(params=params, readback=readback)
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


async def _current_submitted_proposal_identity(
    page,
    proposal_url: str,
) -> dict[str, str] | None:
    """Re-read one proposal identity without navigating away from an open dialog."""

    try:
        _, expected_id = parse_submitted_proposal_url(proposal_url)
        _, live_id = parse_submitted_proposal_url(str(getattr(page, "url", "")))
    except ValueError:
        return None
    if live_id != expected_id:
        return None

    async def one_visible_text(selector: str) -> str | None:
        try:
            candidates = await page.query_selector_all(selector)
        except Exception:
            return None
        visible = [item for item in candidates if await _element_is_visible(item)]
        if len(visible) != 1:
            return None
        try:
            value = _normalise_identity_text(await visible[0].text_content())
        except Exception:
            return None
        return value or None

    job_title = await one_visible_text(_SUBMITTED_PROPOSAL_TITLE_SELECTOR)
    proposal_status = await one_visible_text(_SUBMITTED_PROPOSAL_STATUS_SELECTOR)
    if not job_title or not proposal_status:
        return None
    return {
        "proposal_id": live_id,
        "job_title": job_title,
        "proposal_status": proposal_status,
    }


async def _exact_withdrawal_dialog(page) -> Any | None:
    """Resolve one visible dialog whose own text identifies proposal withdrawal."""

    try:
        dialogs = await page.query_selector_all(
            '[role="dialog"], [data-test="withdraw-proposal-dialog"], .air3-modal'
        )
    except Exception:
        return None
    matches: list[Any] = []
    for dialog in dialogs:
        if not await _element_is_visible(dialog):
            continue
        try:
            text = _normalise_identity_text(await dialog.text_content())
        except Exception:
            continue
        if re.search(r"\bwithdraw(?:ing)?\s+(?:this\s+)?proposal\b|\bproposal\s+withdrawal\b", text, re.I):
            matches.append(dialog)
    return matches[0] if len(matches) == 1 else None


async def _exact_withdraw_control(scope, selector: str) -> Any | None:
    """Resolve one exact Withdraw/Withdraw proposal control, never Yes/Confirm."""

    controls = await _visible_enabled_elements(scope, selector)
    matches: list[Any] = []
    for control in controls:
        try:
            label = _normalise_identity_text(await control.text_content())
        except Exception:
            continue
        if re.fullmatch(r"Withdraw(?: proposal)?", label, re.I):
            matches.append(control)
    return matches[0] if len(matches) == 1 else None


async def _withdrawal_reason_state(
    dialog,
    approved_reason: str | None,
    *,
    fill: bool,
) -> tuple[bool, str]:
    """Fill/read one dialog-scoped reason control, or prove its approved blank state."""

    controls = await _visible_enabled_elements(
        dialog,
        '[data-test*="withdraw-reason"] textarea, textarea[name*="reason"], textarea',
    )
    if len(controls) > 1:
        return False, "Multiple visible withdrawal reason controls made the dialog ambiguous."
    if approved_reason is None:
        if not controls:
            return True, "not_present"
        try:
            return (
                str(await controls[0].input_value()) == "",
                "blank" if str(await controls[0].input_value()) == "" else "unexpected_value",
            )
        except Exception:
            return False, "The withdrawal reason state could not be read."
    if len(controls) != 1:
        return False, "One exact visible withdrawal reason control was not found."
    try:
        if fill:
            await controls[0].fill(approved_reason)
        exact = str(await controls[0].input_value()) == approved_reason
    except Exception:
        exact = False
    return (
        (True, "exact")
        if exact
        else (False, "The approved withdrawal reason could not be read back exactly.")
    )


async def _withdraw_proposal_on_page(params: WithdrawProposalParams, page) -> dict[str, Any]:
    """Withdraw while the browser operation lock is held."""

    current = await _get_proposal_details_on_page(params.proposal_url, page)
    live_identity = _proposal_identity(current)
    approved_identity = {
        "proposal_id": params.proposal_id,
        "job_title": re.sub(r"\s+", " ", params.job_title).strip(),
        "proposal_status": re.sub(r"\s+", " ", params.proposal_status).strip(),
    }
    if live_identity and _proposal_status_is_withdrawn(live_identity["proposal_status"]):
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

    withdraw_btn = await _exact_withdraw_control(
        page,
        '[data-test="withdraw-button"], button:text-is("Withdraw"), '
        'button:text-is("Withdraw proposal")',
    )
    if not withdraw_btn:
        return {
            "status": "error",
            "message": "Withdraw button not found. Proposal may already be closed.",
            "external_action_taken": False,
        }

    await _click(page, withdraw_btn)

    dialog = await _exact_withdrawal_dialog(page)
    if dialog is None:
        return {
            "status": "error",
            "message": "One exact visible withdrawal dialog was not found.",
            "external_action_taken": False,
        }
    reason_ok, reason_state = await _withdrawal_reason_state(
        dialog,
        params.reason,
        fill=True,
    )
    if not reason_ok:
        return {
            "status": "error",
            "message": reason_state,
            "external_action_taken": False,
        }

    # Re-resolve the exact dialog and every approved state immediately before
    # the only irreversible click.  No page-wide Yes/Confirm fallback exists.
    dialog = await _exact_withdrawal_dialog(page)
    live_identity_before_confirm = await _current_submitted_proposal_identity(
        page,
        params.proposal_url,
    )
    reason_ok, final_reason_state = (
        await _withdrawal_reason_state(dialog, params.reason, fill=False)
        if dialog is not None
        else (False, "The exact withdrawal dialog disappeared before confirmation.")
    )
    if dialog is None or live_identity_before_confirm != approved_identity or not reason_ok:
        return {
            "status": "live_identity_mismatch",
            "message": (
                "The exact proposal, withdrawal dialog, or approved reason changed before "
                "confirmation; withdrawal was not confirmed."
            ),
            "approved_proposal_identity": approved_identity,
            "live_proposal_identity": live_identity_before_confirm,
            "withdrawal_reason_state": final_reason_state,
            "external_action_taken": False,
        }
    confirm_btn = await _exact_withdraw_control(
        dialog,
        '[data-test="confirm-withdraw"], button:text-is("Withdraw"), '
        'button:text-is("Withdraw proposal")',
    )
    if not confirm_btn:
        return {
            "status": "error",
            "message": "One exact final Withdraw control was not found in the withdrawal dialog.",
            "external_action_taken": False,
        }
    await _click(page, confirm_btn)

    last_readback_identity: dict[str, str] | None = None
    for _ in range(20):
        try:
            readback_details = await _get_proposal_details_on_page(params.proposal_url, page)
        except Exception:
            readback_details = {}
        readback_identity = _proposal_identity(readback_details)
        last_readback_identity = readback_identity
        same_target = bool(
            readback_identity
            and readback_identity["proposal_id"] == params.proposal_id
            and readback_identity["job_title"] == approved_identity["job_title"]
        )
        if (
            readback_identity is not None
            and same_target
            and _proposal_status_is_withdrawn(readback_identity["proposal_status"])
        ):
            return {
                "status": "withdrawn",
                "message": "Proposal withdrawal read back from Upwork",
                "owner_system_readback": {
                    "confirmed": True,
                    "evidence": (
                        "exact scoped proposal status: "
                        f"{readback_identity['proposal_status']}"
                    ),
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
            "proposal_identity": last_readback_identity,
            "url": str(getattr(page, "url", params.proposal_url)),
        },
        "external_action_taken": True,
    }
