"""Deterministic tests for JRR's Upwork screening policy."""

from datetime import UTC, datetime, timedelta

from upwork_mcp.strategy import (
    PricingContext,
    analyze_job,
    audit_proposals,
    payload_digest,
    validate_proof_claims,
    validate_upwork_copy,
)


def _client():
    return {
        "payment_verified": True,
        "total_spent": 75_000,
        "total_hires": 12,
        "hire_rate": 78,
        "rating": 4.9,
        "avg_hourly_rate_paid": 68,
    }


def test_google_ads_law_job_is_reachable_and_uses_verified_proof():
    result = analyze_job(
        {
            "title": "Google Ads audit for family law firm",
            "description": "Review paid search lead quality and call tracking",
            "job_type": "hourly",
            "hourly_rate_min": 50,
            "hourly_rate_max": 80,
            "proposal_count": "5 to 10",
            "connects_required": 8,
            "client": _client(),
        }
    ).model_dump()

    assert result["recommendation"] in {"strong_fit", "fit"}
    assert result["case_studies"][0]["key"] in {
        "cage-and-miles-family-law",
        "melanson-ssdi-law",
        "drd-criminal-law",
    }
    assert result["pricing"]["recommended_bid"] == 63


def test_gtm_and_lsa_requirements_are_hard_scope_blocks():
    for description in ("Fix our Google Tag Manager container", "Manage Google Ads and Local Services Ads"):
        result = analyze_job({"title": "Google Ads specialist", "description": description, "client": _client()})
        assert result.recommendation == "skip"
        assert result.blockers


def test_service_overlap_alone_does_not_expose_an_unrelated_case_study():
    result = analyze_job(
        {
            "title": "Google Ads account review",
            "description": "Paid search audit for a business whose vertical is not disclosed",
            "job_type": "hourly",
            "hourly_rate_max": 80,
            "client": _client(),
        }
    )
    assert result.case_studies == []


def test_whatconverts_offline_conversion_scope_is_allowed_with_boundary():
    result = analyze_job(
        {
            "title": "Google Ads offline conversion tracking for B2B",
            "description": "Open to WhatConverts for qualified calls and CRM outcomes",
            "job_type": "hourly",
            "hourly_rate_max": 70,
            "proposal_count": 4,
            "client": _client(),
        }
    )
    assert not any("Tag Manager" in blocker for blocker in result.blockers)
    assert any("WhatConverts" in boundary for boundary in result.scope_boundaries)


def test_full_time_agency_role_is_skipped_but_consultancy_is_not():
    full_time = analyze_job(
        {"title": "Google Ads agency lead", "description": "Full-time, 40 hours and direct client ownership", "client": _client()}
    )
    consultancy = analyze_job(
        {
            "title": "White-label Google Ads consultant for agency",
            "description": "Part-time consultancy",
            "job_type": "hourly",
            "hourly_rate_max": 80,
            "proposal_count": 5,
            "client": _client(),
        }
    )
    assert full_time.recommendation == "skip"
    assert not any("employee-style" in blocker for blocker in consultancy.blockers)


def test_invites_are_not_boosted_and_strong_open_jobs_may_be_considered():
    base = {
        "title": "Google Ads for criminal defense law firm",
        "description": "Lead generation and account audit",
        "job_type": "hourly",
        "hourly_rate_max": 100,
        "proposal_count": 3,
        "connects_required": 8,
        "client": _client(),
    }
    invited = analyze_job({**base, "invited": True})
    open_job = analyze_job(base)
    assert invited.boost["recommendation"] == "no_boost"
    if open_job.recommendation == "strong_fit":
        assert open_job.boost["recommendation"] == "inspect_live_auction"
        assert open_job.boost["max_extra_connects"] <= 12


def test_low_rate_is_a_price_conversion_not_an_automatic_discount():
    result = analyze_job(
        {
            "title": "Google Ads audit for law firm",
            "description": "Existing account with paid search lead generation",
            "job_type": "hourly",
            "hourly_rate_min": 30,
            "hourly_rate_max": 55,
            "proposal_count": 5,
            "client": _client(),
        },
        PricingContext(profile_hourly_rate=63, minimum_hourly_rate=50),
    )
    assert result.pricing["recommended_bid"] == 55
    assert result.pricing["position"] == "price_conversion_opportunity"
    assert result.pricing["requires_owner_approval"] is True


def test_fixed_bid_requires_owner_choice_without_a_current_floor():
    result = analyze_job(
        {
            "title": "Google Ads audit",
            "description": "One-off paid search review",
            "job_type": "fixed",
            "budget_max": 300,
            "client": _client(),
        }
    )
    assert result.pricing["recommended_bid"] is None
    assert result.pricing["position"] == "owner_decision_required"


def test_high_client_range_does_not_trigger_an_unnecessary_low_bid():
    result = analyze_job(
        {
            "title": "Google Ads for family law firm",
            "description": "Paid search lead generation",
            "job_type": "hourly",
            "hourly_rate_min": 120,
            "hourly_rate_max": 220,
            "proposal_count": 5,
            "client": _client(),
        }
    )
    assert result.pricing["recommended_bid"] == 120
    assert result.pricing["defensible_range"] == [120, 175]


def test_unaudited_aggregate_claims_are_quarantined():
    result = validate_proof_claims(
        "We've helped clients generate over $100M through Google Ads.",
        [],
    )
    assert result["valid"] is False
    assert "methodology" in result["errors"][0]


def test_exact_selected_case_study_claim_is_allowed_when_attributed():
    selected = [
        {
            "key": "priority-one-plumbing",
            "name": "Priority 1 Plumbing",
            "url": (
                "https://josiahroche.co/digital-marketing-case-studies/"
                "local-plumber-marketing-agency"
            ),
            "approved_claims": ["1,258 tracked leads.", "33% tracked conversion rate."],
        }
    ]
    result = validate_proof_claims(
        (
            "Priority 1 Plumbing is the closest example. It generated 1,258 tracked leads. "
            "https://josiahroche.co/digital-marketing-case-studies/local-plumber-marketing-agency"
        ),
        selected,
    )
    assert result["valid"] is True


def test_paraphrased_or_rounded_case_study_result_is_rejected():
    selected = [
        {
            "key": "japanese-head-spa",
            "name": "Japanese Head Spa",
            "url": "https://josiahroche.co/digital-marketing-case-studies/japanese-spa-marketing",
            "approved_claims": ["844.11% actual ROAS.", "349 tracked leads."],
        }
    ]
    result = validate_proof_claims(
        "Japanese Head Spa generated roughly 844% ROAS.",
        selected,
    )
    assert result["valid"] is False
    assert any("exact permitted claim" in error for error in result["errors"])


def test_exact_claim_cannot_be_hidden_inside_a_larger_number():
    selected = [
        {
            "key": "priority-one-plumbing",
            "name": "Priority 1 Plumbing",
            "url": (
                "https://josiahroche.co/digital-marketing-case-studies/"
                "local-plumber-marketing-agency"
            ),
            "approved_claims": ["1,258 tracked leads."],
        }
    ]
    result = validate_proof_claims(
        "Priority 1 Plumbing generated 11,258 tracked leads.",
        selected,
    )
    assert result["valid"] is False
    assert any("exact permitted claim" in error for error in result["errors"])


def test_exact_numeric_result_must_identify_or_link_the_case_study():
    selected = [
        {
            "key": "dark-shade-window-tinting",
            "name": "Dark Shade Window Tinting",
            "url": (
                "https://josiahroche.co/digital-marketing-case-studies/"
                "window-tinting-marketing-houston"
            ),
            "approved_claims": ["10.63x Google Ads ROAS."],
        }
    ]
    result = validate_proof_claims("A similar account reached 10.63x Google Ads ROAS.", selected)
    assert result["valid"] is False
    assert any("identify or link" in error for error in result["errors"])


def test_experience_years_and_bid_rate_are_not_treated_as_results():
    result = validate_proof_claims(
        "I've worked in Google Ads for 10 years, and the proposed rate is $63/hr.",
        [],
    )
    assert result["valid"] is True


def test_copy_validator_blocks_off_platform_and_template_style_copy():
    result = validate_upwork_copy(
        "Hey, more than happy to take a look at this.\n\nI'm an expert. Book at calendly.com/example.",
        invited=False,
    )
    assert result["valid"] is False
    assert len(result["errors"]) >= 2


def test_payload_digest_changes_when_commercial_terms_change():
    first = payload_digest({"job_url": "https://www.upwork.com/jobs/~abc", "rate": 63, "cover_letter": "Exact copy"})
    second = payload_digest({"job_url": "https://www.upwork.com/jobs/~abc", "rate": 64, "cover_letter": "Exact copy"})
    assert first != second


def test_old_unviewed_proposals_are_not_withdrawn_for_cosmetic_cleanup():
    submitted = (datetime.now(UTC) - timedelta(days=30)).strftime("%Y-%m-%d")
    result = audit_proposals([{"job_title": "Old job", "submitted": submitted, "client_viewed": False}])
    assert result["proposals"][0]["maintenance_action"] == "leave_unwithdrawn"
