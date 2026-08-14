"""Read-only management workflows and approval preflights for JRR Upwork work."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..ledger import POLICY_VERSION, record_screening
from ..prepared_actions import prepare_action
from ..strategy import (
    PricingContext,
    analyze_job,
    audit_proposals,
    payload_digest,
    validate_proof_claims,
    validate_upwork_copy,
)
from .jobs import JobDetailsParams, JobSearchParams, get_job_details, search_jobs
from .proposals import (
    FixedPriceMilestone,
    InspectProposalCommercialPreflightParams,
    InspectProposalFormParams,
    ProposalsParams,
    get_proposals,
    inspect_proposal_commercial_preflight,
    inspect_proposal_form,
    normalize_live_context_lines,
    parse_job_url,
    validate_payment_terms,
)


class PrepareProposalParams(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    job_url: str = Field(min_length=2, max_length=500)
    cover_letter: str = Field(min_length=1, max_length=10_000)
    rate: float | None = Field(default=None, ge=50)
    bid: float | None = Field(default=None, gt=0)
    payment_structure: Literal["by_project", "by_milestone"] | None = None
    milestones: list[FixedPriceMilestone] = Field(default_factory=list, max_length=1)
    answers: list[str] = Field(default_factory=list, max_length=20)
    duration: Literal[
        "Less than 1 month",
        "1 to 3 months",
        "3 to 6 months",
        "More than 6 months",
    ] | None = None
    profile_highlights: list[str] = Field(default_factory=list, max_length=4)
    boost_connects: int = Field(default=0, ge=0)
    rate_increase_frequency: Literal["Never"] = "Never"
    profile_hourly_rate: float = Field(default=63, ge=50)
    minimum_hourly_rate: float = Field(default=50, ge=50)
    minimum_fixed_fee: float | None = Field(default=None, gt=0)

    @field_validator("answers")
    @classmethod
    def _answers_must_not_be_blank(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("Screening answers cannot be blank")
        return values

    @field_validator("profile_highlights")
    @classmethod
    def _profile_highlights_must_be_distinct(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("Profile highlights cannot be blank")
        identities = [re.sub(r"\s+", " ", value).strip().casefold() for value in values]
        if len(identities) != len(set(identities)):
            raise ValueError("Profile highlights cannot contain duplicates")
        return values

    @model_validator(mode="after")
    def validate_terms(self) -> PrepareProposalParams:
        self.job_url = parse_job_url(self.job_url)[0]
        validate_payment_terms(
            rate=self.rate,
            bid=self.bid,
            payment_structure=self.payment_structure,
            milestones=self.milestones,
        )
        if self.payment_structure == "by_milestone":
            raise ValueError("Automated proposal preparation supports fixed by_project terms only")
        if self.profile_hourly_rate < self.minimum_hourly_rate:
            raise ValueError("profile_hourly_rate cannot be below minimum_hourly_rate")
        if self.rate is not None and self.rate < self.minimum_hourly_rate:
            raise ValueError("The approved hourly rate cannot be below minimum_hourly_rate")
        return self


def _proposal_payload(
    params: PrepareProposalParams,
    *,
    base_connects: int | None,
    form: dict[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "job_url": params.job_url,
        "job_id": form.get("job_id"),
        "form_url": form.get("form_url"),
        "job_title": form.get("job_title"),
        "job_type": form.get("job_type"),
        "cover_letter": params.cover_letter,
        "fee_net_text": normalize_live_context_lines(form.get("fee_net_text") or []),
        "fee_net_status": form.get("fee_net_status"),
        "fee_net_price_amount": form.get("fee_net_price_amount"),
        "fee_net_source": form.get("fee_net_source"),
        "boost_auction_text": normalize_live_context_lines(form.get("boost_auction_text") or []),
        "boost_auction_status": form.get("boost_auction_status"),
        "rate": params.rate,
        "bid": params.bid,
        "payment_structure": params.payment_structure,
        "milestones": [item.model_dump(mode="json") for item in params.milestones],
        "answers": params.answers,
        "screening_questions": form.get("screening_questions") or [],
        "screening_questions_status": form.get("screening_questions_status"),
        "duration": params.duration,
        "duration_options_status": form.get("duration_options_status"),
        "profile_highlights": params.profile_highlights,
        "available_profile_highlights_status": form.get(
            "available_profile_highlights_status"
        ),
        "base_connects_status": form.get("base_connects_status"),
        "boost_connects": params.boost_connects,
        "rate_increase_frequency": params.rate_increase_frequency,
        "rate_increase_control_status": form.get("rate_increase_control_status"),
    }
    if base_connects is not None:
        payload["base_connects"] = base_connects
    return payload


async def prepare_proposal(params: PrepareProposalParams) -> dict[str, Any]:
    """Build the exact owner-approval artifact without submitting anything."""
    job = await get_job_details(JobDetailsParams(job_url=params.job_url))
    form = await inspect_proposal_form(InspectProposalFormParams(job_url=params.job_url))
    analysis = analyze_job(
        job,
        PricingContext(
            profile_hourly_rate=params.profile_hourly_rate,
            minimum_hourly_rate=params.minimum_hourly_rate,
            minimum_fixed_fee=params.minimum_fixed_fee,
        ),
    ).model_dump()
    copy_check = validate_upwork_copy(params.cover_letter, invited=bool(job.get("invited")))
    errors = list(copy_check["errors"])
    warnings = list(copy_check["warnings"])
    proof_check = validate_proof_claims(params.cover_letter, analysis["case_studies"])
    errors.extend(proof_check["errors"])

    expected_job_id = parse_job_url(params.job_url)[1]
    job_title = " ".join(str(job.get("title") or "").split())
    form_title = " ".join(str(form.get("job_title") or "").split())
    expected_job_type = "hourly" if params.rate is not None else "fixed"
    can_run_commercial_preflight = bool(
        form.get("form_status") == "ready"
        and not form.get("existing_proposal")
        and form.get("job_id") == expected_job_id
        and form.get("form_url")
        and job_title
        and form_title == job_title
        and form.get("job_type") == expected_job_type
        and analysis["recommendation"] not in {"skip", "scope_review"}
        and not errors
        and params.duration in (form.get("duration_options") or [])
        and form.get("duration_options_status") == "complete"
        and bool(params.profile_highlights)
        and form.get("available_profile_highlights_status") == "complete"
        and set(params.profile_highlights).issubset(
            set(form.get("available_profile_highlights") or [])
        )
        and form.get("screening_questions_status") == "complete"
        and len(form.get("screening_questions") or []) == len(params.answers)
        and form.get("base_connects_status") == "complete"
        and form.get("base_connects") is not None
        and params.boost_connects == 0
        and (
            form.get("rate_increase_control_status") == "complete"
            if expected_job_type == "hourly"
            else form.get("rate_increase_control_status") == "not_applicable"
        )
        and (
            params.bid is None
            or params.payment_structure in (form.get("fixed_payment_structures") or [])
        )
    )
    commercial_preflight: dict[str, Any] | None = None
    if can_run_commercial_preflight:
        try:
            commercial_preflight = await inspect_proposal_commercial_preflight(
                InspectProposalCommercialPreflightParams(
                    job_url=str(form["form_url"]),
                    rate=params.rate,
                    bid=params.bid,
                    payment_structure=(
                        "by_project" if params.payment_structure == "by_project" else None
                    ),
                )
            )
        except Exception as error:
            commercial_preflight = {
                "fee_net_text": [],
                "fee_net_status": "unavailable",
                "fee_net_price_amount": None,
                "fee_net_source": None,
                "price_restored": False,
                "identity_restored": False,
                "external_action_taken": True,
                "fee_net_details": {
                    "message": (
                        "The reversible commercial preflight failed closed: "
                        f"{type(error).__name__}."
                    )
                },
            }

    if commercial_preflight is not None:
        approved_amount = Decimal(
            str(params.rate if params.rate is not None else params.bid)
        ).quantize(Decimal("0.01"))
        preflight_identity_matches = bool(
            commercial_preflight.get("job_url") == params.job_url
            and commercial_preflight.get("job_id") == form.get("job_id")
            and commercial_preflight.get("form_url") == form.get("form_url")
            and " ".join(str(commercial_preflight.get("job_title") or "").split())
            == form_title
            and commercial_preflight.get("job_type") == form.get("job_type")
            and commercial_preflight.get("form_status") == "ready"
            and not commercial_preflight.get("existing_proposal")
            and commercial_preflight.get("price_restored") is True
            and commercial_preflight.get("identity_restored") is True
            and commercial_preflight.get("external_action_taken") is False
            and commercial_preflight.get("fee_net_status") == "complete"
            and commercial_preflight.get("fee_net_price_amount")
            == format(approved_amount, ".2f")
            and commercial_preflight.get("fee_net_source")
            == "scoped_reversible_price_preflight"
        )
        form = {
            **form,
            "commercial_preflight": commercial_preflight,
            "fee_net_text": (
                commercial_preflight.get("fee_net_text") or []
                if preflight_identity_matches
                else []
            ),
            "fee_net_status": (
                "complete" if preflight_identity_matches else "incomplete"
            ),
            "fee_net_price_amount": (
                commercial_preflight.get("fee_net_price_amount")
                if preflight_identity_matches
                else None
            ),
            "fee_net_source": (
                commercial_preflight.get("fee_net_source")
                if preflight_identity_matches
                else None
            ),
            "fee_net_details": commercial_preflight.get("fee_net_details") or {},
            "external_action_taken": bool(
                form.get("external_action_taken")
                or commercial_preflight.get("external_action_taken")
            ),
        }
    if not expected_job_id or form.get("job_id") != expected_job_id:
        errors.append("The live application form does not match the exact requested job ID")
    if not job_title or not form_title or job_title != form_title:
        errors.append("The live job title and application-form title could not be matched exactly")
    if not form.get("form_url"):
        errors.append("The canonical individual application-form URL could not be bound")
    if form.get("job_type") not in {"hourly", "fixed"}:
        errors.append("The live application job type could not be bound")
    elif params.rate is not None and form.get("job_type") != "hourly":
        errors.append("The approved hourly rate does not match the live fixed-price form")
    elif params.bid is not None and form.get("job_type") != "fixed":
        errors.append("The approved fixed bid does not match the live hourly form")
    if params.bid is not None:
        available_structures = form.get("fixed_payment_structures") or []
        if params.payment_structure not in available_structures:
            errors.append("The approved fixed-price payment structure is not available in the live form")

    if analysis["recommendation"] in {"skip", "scope_review"}:
        errors.append(
            "The JRR screening policy requires skip or manual scope review before this job can be prepared"
        )
    if params.rate is not None and params.bid is not None:
        errors.append("Choose either an hourly rate or a fixed bid, not both")
    if params.rate is None and params.bid is None:
        errors.append("The exact hourly rate or fixed bid is required before approval")
    if not params.duration:
        errors.append("Project duration is required because Upwork can silently clear it")
    if params.rate_increase_frequency.lower() != "never":
        errors.append('Rate increase frequency must be set to "Never"')
    if len(params.profile_highlights) > 4:
        errors.append("Upwork allows no more than 4 profile highlights")
    if not params.profile_highlights:
        errors.append("Select at least one current owner-system profile highlight before approval")
    form_ready = form.get("form_status") == "ready"
    if not form_ready:
        errors.append(f"The live proposal form is not ready: {form.get('form_status')}")
    screening_questions_status = form.get("screening_questions_status")
    if form_ready and screening_questions_status != "complete":
        errors.append(
            "Live screening-question enumeration is not complete, so answers cannot be bound for approval"
        )
    duration_options_status = form.get("duration_options_status")
    if form_ready and duration_options_status != "complete":
        errors.append(
            "Live duration-option enumeration is not complete, so the selected duration cannot be approved"
        )
    fee_net_status = form.get("fee_net_status")
    if form_ready and fee_net_status != "complete":
        errors.append(
            "The live fee/net preview is not complete, so the exact proposal price context cannot be approved"
        )
    rate_increase_control_status = form.get("rate_increase_control_status")
    if form_ready and rate_increase_control_status not in {"complete", "not_applicable"}:
        errors.append(
            "Live rate-increase control applicability is incomplete, so the required Never setting cannot be approved"
        )
    if (
        form_ready
        and form.get("job_type") == "fixed"
        and rate_increase_control_status != "not_applicable"
    ):
        errors.append(
            "The fixed-price form did not explicitly bind rate-increase controls as not_applicable"
        )
    available_highlights_status = form.get("available_profile_highlights_status")
    available_highlights = set(form.get("available_profile_highlights") or [])
    if form_ready and available_highlights_status != "complete":
        errors.append(
            "Live profile-highlight enumeration is not complete, so highlight titles cannot be validated for approval"
        )
    elif form_ready:
        invalid_highlights = [
            highlight for highlight in params.profile_highlights if highlight not in available_highlights
        ]
        if invalid_highlights:
            errors.append(
                "These profile highlights are not selectable in the live form: " + ", ".join(invalid_highlights)
            )
    if form.get("existing_proposal"):
        errors.append("An existing proposal was found for this job")
    screening_questions = form.get("screening_questions") or []
    if screening_questions_status == "complete" and len(screening_questions) != len(params.answers):
        errors.append(
            f"The live form has {len(screening_questions)} screening questions but {len(params.answers)} answers were supplied"
        )
    duration_options = form.get("duration_options") or []
    if duration_options_status == "complete" and params.duration not in duration_options:
        errors.append("The selected duration is not available in the live form")
    if form.get("base_connects") is None:
        errors.append("The live base Connect cost could not be verified")
    if form.get("base_connects_status") != "complete":
        errors.append("Exact scoped base-Connect discovery is not complete")
    if fee_net_status == "complete" and not form.get("fee_net_text"):
        errors.append("The complete live fee/net inspection did not contain any normalized context")
    if commercial_preflight is None:
        errors.append("The exact reversible commercial preflight could not be started")
    elif form.get("fee_net_source") != "scoped_reversible_price_preflight":
        errors.append("The live fee/net preview was not bound to the exact approved price")
    if form.get("external_action_taken"):
        errors.append(
            "The commercial preflight could not prove restoration of the original live form; inspect Upwork manually"
        )

    recommended = set(analysis["profile_highlights"])
    if form_ready and available_highlights_status == "complete":
        unavailable_recommended = sorted(recommended - available_highlights)
        if unavailable_recommended:
            warnings.append(
                "Policy-suggested highlights are not selectable in the live form: "
                + ", ".join(unavailable_recommended)
            )
        recommended &= available_highlights
    supplied = set(params.profile_highlights)
    missing_recommended = sorted(recommended - supplied)
    if missing_recommended:
        warnings.append(f"Recommended highlights not selected: {', '.join(missing_recommended)}")
    if params.boost_connects and analysis["boost"]["recommendation"] != "inspect_live_auction":
        errors.append("This job does not meet the selective-boost policy")
    if params.boost_connects > analysis["boost"]["max_extra_connects"]:
        errors.append("Boost exceeds the policy cap for this job")
    if params.boost_connects:
        errors.append(
            "Positive-boost proposal preparation is disabled until the live Upwork flow can prove that the first Submit click is non-consequential."
        )
    boost_auction_status = form.get("boost_auction_status")
    if params.boost_connects and boost_auction_status != "complete":
        errors.append("A nonzero boost requires a complete live boost-auction inspection")
    if boost_auction_status == "complete" and not form.get("boost_auction_text"):
        errors.append("The complete live boost-auction inspection did not contain any normalized context")

    payload = _proposal_payload(params, base_connects=form.get("base_connects"), form=form)
    prepared_action = prepare_action("proposal", payload) if not errors else None
    return {
        "ready_for_owner_approval": not errors,
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys(warnings)),
        "job": job,
        "live_form": form,
        "analysis": analysis,
        "exact_submission": payload,
        "approval_sha256": payload_digest(payload),
        "prepared_action": prepared_action,
        "copy_sha256": copy_check["copy_sha256"],
        "external_action_taken": bool(form.get("external_action_taken")),
        "next_step": "Show the exact copy, answers, fee, duration, highlights, Connect cost, and boost choice to Josiah for approval",
    }


async def find_opportunities(
    query: str,
    *,
    limit_per_view: int = 5,
    include_skips: bool = False,
    profile_hourly_rate: float = 63,
    minimum_hourly_rate: float = 50,
    minimum_fixed_fee: float | None = None,
) -> dict[str, Any]:
    """Scan both discovery views, hydrate each unique post, and rank it."""
    found: list[dict[str, Any]] = []
    for mode in ("best_matches", "most_recent"):
        jobs = await search_jobs(JobSearchParams(query=query, search_mode=mode, limit=limit_per_view))
        found.extend(jobs)

    unique: dict[str, tuple[str, dict[str, Any]]] = {}
    invalid_job_urls = 0
    for job in found:
        raw_url = str(job.get("url") or "").strip()
        if raw_url.startswith("~"):
            raw_url = f"https://www.upwork.com/jobs/{raw_url}"
        elif raw_url.startswith("/jobs/"):
            raw_url = f"https://www.upwork.com{raw_url}"
        try:
            canonical_url, job_id = parse_job_url(raw_url)
        except ValueError:
            invalid_job_urls += 1
            continue
        unique.setdefault(job_id, (canonical_url, job))

    ranked: list[dict[str, Any]] = []
    for canonical_url, summary in unique.values():
        details = await get_job_details(JobDetailsParams(job_url=canonical_url))
        details["url"] = canonical_url
        details["discovered_in"] = summary.get("search_mode")
        analysis = analyze_job(
            details,
            PricingContext(
                profile_hourly_rate=profile_hourly_rate,
                minimum_hourly_rate=minimum_hourly_rate,
                minimum_fixed_fee=minimum_fixed_fee,
            ),
        ).model_dump()
        ledger = record_screening(details, analysis, policy_version=POLICY_VERSION)
        if include_skips or analysis["recommendation"] != "skip":
            ranked.append({"job": details, "analysis": analysis, "local_ledger": ledger})

    order = {
        "strong_fit": 0,
        "price_conversion": 1,
        "fit": 2,
        "speculative": 3,
        "scope_review": 4,
        "skip": 5,
    }
    ranked.sort(
        key=lambda item: (
            order.get(item["analysis"]["recommendation"], 9),
            -item["analysis"]["score"],
        )
    )
    return {
        "query": query,
        "views_scanned": ["best_matches", "most_recent"],
        "unique_jobs_reviewed": len(unique),
        "invalid_job_urls_skipped": invalid_job_urls,
        "opportunities": ranked,
        "external_action_taken": False,
        "policy_version": POLICY_VERSION,
    }


async def audit_live_proposals(
    status: Literal["active", "submitted", "archived", "all"] = "all",
    limit: int = 50,
    stale_after_days: int = 14,
) -> dict[str, Any]:
    proposals = await get_proposals(ProposalsParams(status=status, limit=limit))
    result = audit_proposals(proposals, stale_after_days=stale_after_days)
    result["external_action_taken"] = False
    return result
