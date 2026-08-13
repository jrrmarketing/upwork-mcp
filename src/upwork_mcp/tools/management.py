"""Read-only management workflows and approval preflights for JRR Upwork work."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
from .proposals import InspectProposalFormParams, ProposalsParams, get_proposals, inspect_proposal_form


class PrepareProposalParams(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    job_url: str = Field(min_length=2, max_length=500)
    cover_letter: str = Field(min_length=1, max_length=8_000)
    rate: float | None = Field(default=None, gt=0)
    bid: float | None = Field(default=None, gt=0)
    answers: list[str] = Field(default_factory=list, max_length=20)
    duration: str | None = Field(default=None, max_length=100)
    profile_highlights: list[str] = Field(default_factory=list, max_length=4)
    boost_connects: int = Field(default=0, ge=0)
    rate_increase_frequency: str = "Never"
    profile_hourly_rate: float = Field(default=63, gt=0)
    minimum_hourly_rate: float = Field(default=50, gt=0)
    minimum_fixed_fee: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_terms(self) -> PrepareProposalParams:
        if (self.rate is None) == (self.bid is None):
            raise ValueError("Choose exactly one of rate or bid")
        if self.rate_increase_frequency != "Never":
            raise ValueError('rate_increase_frequency must be "Never"')
        return self


def _proposal_payload(params: PrepareProposalParams, *, base_connects: int | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "job_url": params.job_url,
        "cover_letter": params.cover_letter,
        "rate": params.rate,
        "bid": params.bid,
        "answers": params.answers,
        "screening_questions": [],
        "duration": params.duration,
        "profile_highlights": params.profile_highlights,
        "boost_connects": params.boost_connects,
        "rate_increase_frequency": params.rate_increase_frequency,
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

    if analysis["recommendation"] == "skip":
        errors.append("The JRR screening policy classifies this job as skip")
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
    if form.get("form_status") != "ready":
        errors.append(f"The live proposal form is not ready: {form.get('form_status')}")
    if form.get("existing_proposal"):
        errors.append("An existing proposal was found for this job")
    screening_questions = form.get("screening_questions") or []
    if len(screening_questions) != len(params.answers):
        errors.append(
            f"The live form has {len(screening_questions)} screening questions but {len(params.answers)} answers were supplied"
        )
    duration_options = form.get("duration_options") or []
    if duration_options and params.duration not in duration_options:
        errors.append("The selected duration is not available in the live form")
    if form.get("base_connects") is None:
        errors.append("The live base Connect cost could not be verified")
    if not form.get("fee_net_text"):
        warnings.append("Upwork did not expose a live fee/net preview during read-only inspection")

    recommended = set(analysis["profile_highlights"])
    supplied = set(params.profile_highlights)
    missing_recommended = sorted(recommended - supplied)
    if missing_recommended:
        warnings.append(f"Recommended highlights not selected: {', '.join(missing_recommended)}")
    if params.boost_connects and analysis["boost"]["recommendation"] != "inspect_live_auction":
        errors.append("This job does not meet the selective-boost policy")
    if params.boost_connects > analysis["boost"]["max_extra_connects"]:
        errors.append("Boost exceeds the policy cap for this job")

    payload = _proposal_payload(params, base_connects=form.get("base_connects"))
    payload["screening_questions"] = form.get("screening_questions") or []
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
        "external_action_taken": False,
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

    unique: dict[str, dict[str, Any]] = {}
    for job in found:
        unique.setdefault(str(job.get("url")), job)

    ranked: list[dict[str, Any]] = []
    for summary in unique.values():
        details = await get_job_details(JobDetailsParams(job_url=str(summary["url"])))
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

    order = {"strong_fit": 0, "price_conversion": 1, "fit": 2, "speculative": 3, "skip": 4}
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
