"""Deterministic tests for JRR's Upwork screening policy."""

from datetime import UTC, datetime, timedelta

import pytest

from upwork_mcp.strategy import (
    PricingContext,
    analyze_job,
    audit_proposals,
    payload_digest,
    proposal_safe_proof_lines,
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
    assert result["case_studies"][0]["proposal_safe_proof_lines"]
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


def test_proof_matcher_uses_phrase_boundaries_for_law_and_spa_terms():
    result = analyze_job(
        {
            "title": "Flawless paid search for a Spanish aerospace brand",
            "description": "Audit the paid search account and report findings",
            "job_type": "hourly",
            "hourly_rate_max": 80,
            "proposal_count": 4,
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
        {
            "title": "Google Ads agency lead",
            "description": "Full-time, 40 hours and direct client ownership",
            "client": _client(),
        }
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


def test_zero_client_metrics_are_observed_and_cannot_create_a_strong_fit():
    result = analyze_job(
        {
            "title": "Google Ads audit for criminal defense law firm",
            "description": "Paid search lead generation and account review",
            "job_type": "hourly",
            "hourly_rate_max": 100,
            "proposal_count": 0,
            "connects_required": 8,
            "client": {
                "payment_verified": True,
                "total_spent": 0,
                "total_hires": 0,
                "hire_rate": 0,
                "rating": 4.9,
                "avg_hourly_rate_paid": 0,
            },
        }
    )
    components = {component.name: component.points for component in result.components}

    assert components["client_spend"] == -2
    assert components["hire_rate"] == -5
    assert components["low_average_rate"] == -6
    assert components["competition"] == 12
    assert "client total spend" not in result.missing_evidence
    assert "client hire rate" not in result.missing_evidence
    assert result.recommendation != "strong_fit"
    assert result.boost["recommendation"] == "no_boost"


def test_unknown_competition_never_recommends_a_boost():
    result = analyze_job(
        {
            "title": "Google Ads audit for criminal defense law firm",
            "description": "Paid search lead generation and account review",
            "job_type": "hourly",
            "hourly_rate_max": 100,
            "connects_required": 8,
            "client": _client(),
        }
    )

    assert "live proposal count" in result.missing_evidence
    assert result.boost["recommendation"] == "no_boost"
    assert result.boost["max_extra_connects"] == 0


def test_explicit_scope_exclusions_do_not_trigger_hard_skips():
    for description in (
        "Do not use GTM; use WhatConverts for offline conversion outcomes.",
        "This is not a full-time role; the consultant will work five hours a week.",
        "This ecommerce account does not need purchase tracking; Google Ads management only.",
    ):
        result = analyze_job(
            {
                "title": "Google Ads consultant for family law firm",
                "description": description,
                "job_type": "hourly",
                "hourly_rate_max": 80,
                "proposal_count": 5,
                "client": _client(),
            }
        )
        assert not result.blockers, description


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


def test_pricing_context_rejects_a_profile_rate_below_the_floor():
    with pytest.raises(ValueError, match="profile_hourly_rate"):
        PricingContext(profile_hourly_rate=49, minimum_hourly_rate=50)


def test_unaudited_aggregate_claims_are_quarantined():
    result = validate_proof_claims(
        "We've helped clients generate over $100M through Google Ads.",
        [],
    )
    assert result["valid"] is False
    assert "methodology" in result["errors"][0]


def _priority_proof():
    return {
        "key": "priority-one-plumbing",
        "name": "Priority 1 Plumbing",
        "url": ("https://josiahroche.co/digital-marketing-case-studies/local-plumber-marketing-agency"),
        "approved_claims": ["1,258 tracked leads.", "33% tracked conversion rate."],
        "claim_evidence": [
            {
                "text": "1,258 tracked leads.",
                "period": "September 2023 to July 2024.",
            },
            {
                "text": "33% tracked conversion rate.",
                "period": "September 2023 to July 2024.",
            },
        ],
    }


def test_proposal_safe_proof_lines_are_claim_local_and_period_bound():
    selected = [_priority_proof()]
    lines = proposal_safe_proof_lines(selected[0])
    assert lines[0]["line"] == ("A relevant example is Priority 1 Plumbing: 1,258 tracked leads.")
    assert lines[0]["line_with_period"] == (
        "A relevant example is Priority 1 Plumbing: 1,258 tracked leads. Period: September 2023 to July 2024."
    )
    assert validate_proof_claims(lines[0]["line"], selected)["valid"] is True
    assert validate_proof_claims(lines[0]["line_with_period"], selected)["valid"] is True
    full_copy = f"Hey, more than happy to take a look at this.\n\n{lines[0]['line']}"
    assert validate_upwork_copy(full_copy, invited=False)["valid"] is True


def test_proposal_plan_and_validators_never_encourage_external_case_study_urls():
    analysis = analyze_job(
        {
            "title": "Google Ads audit for family law firm",
            "description": "Paid search lead generation",
            "job_type": "hourly",
            "hourly_rate_max": 90,
            "proposal_count": 5,
            "client": _client(),
        }
    )
    assert "case_study_link" not in analysis.proposal_plan
    assert analysis.proposal_plan["external_case_study_links_allowed"] is False

    external_url = _priority_proof()["url"]
    copy_result = validate_upwork_copy(
        f"Hey, more than happy to take a look at this.\n\nSee {external_url}",
        invited=False,
    )
    assert copy_result["valid"] is False
    assert any("external URLs" in error for error in copy_result["errors"])

    safe_line = proposal_safe_proof_lines(_priority_proof())[0]["line"]
    proof_result = validate_proof_claims(f"{safe_line}\n{external_url}", [_priority_proof()])
    assert proof_result["valid"] is False
    assert any("external URLs" in error for error in proof_result["errors"])


def test_proof_line_tampering_and_global_attribution_are_rejected():
    selected = [_priority_proof()]
    safe_line = proposal_safe_proof_lines(selected[0])[0]["line"]
    messages = (
        safe_line.replace("Priority 1 Plumbing", "Acme"),
        safe_line.replace("1,258", "11,258"),
        safe_line.replace("tracked leads", "tracked sales"),
        f"{safe_line} That happened in 30 days.",
        f"{safe_line}\nThat happened in 30 days.",
        "Priority 1 Plumbing is one example. Acme generated 1,258 tracked leads.",
        "Acme generated 1,258 tracked leads. See Priority 1 Plumbing for another example.",
    )
    for message in messages:
        result = validate_proof_claims(message, selected)
        assert result["valid"] is False, message
        assert result["errors"], message

    # Generic copy that does not name or reproduce manifest proof is still shown
    # verbatim to the owner and governed by the exact approval gate.
    assert validate_proof_claims("You should note the case study's 500% ROI.", selected)["valid"] is True


def test_safe_proof_line_does_not_block_normal_scope_or_availability():
    selected = [_priority_proof()]
    safe_line = proposal_safe_proof_lines(selected[0])[0]["line"]
    message = (
        f"{safe_line}\n"
        "I would review your 3 campaigns and compare 2 options for lead generation.\n"
        "I am available for 20 hours per week and the proposed rate is $63/hr."
    )
    assert validate_proof_claims(message, selected)["valid"] is True


def test_no_proof_copy_keeps_free_scope_experience_and_commercial_language():
    messages = (
        "I am available for 20 hours per week to work on lead generation.",
        "I have 10 years of lead generation experience.",
        "I've been generating leads for clients for 10 years.",
        "The fixed bid is $500 and I can complete the audit in 2 weeks.",
    )
    for message in messages:
        assert validate_proof_claims(message, [])["valid"] is True, message


def test_quarantined_aggregate_is_normalized_before_matching():
    result = validate_proof_claims("We’ve made ＄１００Ｍ＋.", [])
    assert result["valid"] is False
    assert any("methodology" in error for error in result["errors"])


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
    assert result["errors"]

    # Raw generic claims have no manifest provenance. They are not auto-generated
    # evidence, so they remain subject to the exact owner-copy approval gate.
    unscoped = validate_proof_claims("A similar account had ROAS of 3x.", selected)
    assert unscoped["valid"] is True


def test_exact_claim_cannot_be_hidden_inside_a_larger_number():
    selected = [
        {
            "key": "priority-one-plumbing",
            "name": "Priority 1 Plumbing",
            "url": ("https://josiahroche.co/digital-marketing-case-studies/local-plumber-marketing-agency"),
            "approved_claims": ["1,258 tracked leads."],
        }
    ]
    result = validate_proof_claims(
        "Priority 1 Plumbing generated 11,258 tracked leads.",
        selected,
    )
    assert result["valid"] is False
    assert result["errors"]


def test_exact_claim_cannot_share_a_sentence_with_an_invented_metric():
    selected = [
        {
            "key": "priority-one-plumbing",
            "name": "Priority 1 Plumbing",
            "url": ("https://josiahroche.co/digital-marketing-case-studies/local-plumber-marketing-agency"),
            "approved_claims": ["1,258 tracked leads."],
        }
    ]
    for extra in ("and achieved a 99% conversion rate", "with 500% ROI", "in 30 days"):
        result = validate_proof_claims(
            f"Priority 1 Plumbing generated 1,258 tracked leads {extra}.",
            selected,
        )
        assert result["valid"] is False
        assert result["errors"]


def test_adjacent_and_written_proof_bypasses_are_rejected():
    selected = [
        {
            "key": "priority-one-plumbing",
            "name": "Priority 1 Plumbing",
            "url": ("https://josiahroche.co/digital-marketing-case-studies/local-plumber-marketing-agency"),
            "approved_claims": ["1,258 tracked leads."],
            "claim_evidence": [
                {
                    "text": "1,258 tracked leads.",
                    "period": "September 2023 to July 2024.",
                }
            ],
        }
    ]
    messages = (
        "Priority 1 Plumbing generated 1,258 tracked leads. That happened in 30 days.",
        "Priority 1 Plumbing generated 1,258 tracked leads. Performance improved by 99%.",
        "Priority 1 Plumbing generated 1,258 tracked leads. It converted 99% better.",
        "Priority 1 Plumbing generated 1,258 tracked leads in thirty days.",
        "Priority 1 Plumbing generated 1,258 tracked leads in one month.",
        "Priority 1 Plumbing generated 1,258 tracked leads. September 2020 to July 2021.",
        "Priority 1 Plumbing generated 1,258 tracked leads. Performance improved by ninety-nine percent.",
        "Priority 1 Plumbing generated 1,258 tracked leads in one hundred and twenty days.",
        "Priority 1 Plumbing generated 1,258 tracked leads in a hundred days.",
        "Priority 1 Plumbing generated 1,258 tracked leads during the first month.",
        "Priority 1 Plumbing generated 1,258 tracked leads in one quarter.",
        "Priority 1 Plumbing generated 1,258 tracked leads. September 2020–July 2021.",
        "Priority 1 Plumbing generated 1,258 tracked leads. Between September 2020 and July 2021.",
        "Priority 1 Plumbing generated 1,258 tracked leads. Outcomes rose by 99%.",
        "Priority 1 Plumbing generated 1,258 tracked leads. 99% more became customers.",
        "Priority 1 Plumbing doubled revenue.",
        "Priority 1 Plumbing tripled ROAS.",
        "Priority 1 Plumbing: Every call converted.",
        "Priority 1 Plumbing generated 1,258 tracked leads and doubled revenue.",
        "Priority 1 Plumbing generated more leads.",
        "Priority 1 Plumbing generated 1,258 tracked leads per month.",
        "Priority 1 Plumbing generated 1,258 tracked leads monthly.",
        "Priority 1 Plumbing generated 1,258 tracked leads. That happened in a month.",
        "Priority 1 Plumbing generated 1,258 tracked leads. That happened within a month.",
    )
    for message in messages:
        result = validate_proof_claims(message, selected)
        assert result["valid"] is False, message
        assert result["errors"], message


def test_case_proof_does_not_block_experience_years_or_bid_rate():
    selected = [_priority_proof()]
    safe_line = proposal_safe_proof_lines(selected[0])[0]["line"]
    result = validate_proof_claims(
        (f"{safe_line}\nI've worked in Google Ads for 10 years, and the proposed rate is $63/hr."),
        selected,
    )
    assert result["valid"] is True

    for scope_sentence in (
        "I would review your 3 campaigns and improve performance.",
        "I'd compare 2 options for lead generation.",
        "I can complete the audit in 2 weeks.",
    ):
        result = validate_proof_claims(
            f"{safe_line}\n{scope_sentence}",
            selected,
        )
        assert result["valid"] is True, scope_sentence

    result = validate_proof_claims(
        (f"{safe_line}\nWith 10 years of experience, I can improve results at $63/hr."),
        selected,
    )
    assert result["valid"] is True


def test_exact_numeric_result_must_identify_or_link_the_case_study():
    selected = [
        {
            "key": "dark-shade-window-tinting",
            "name": "Dark Shade Window Tinting",
            "url": ("https://josiahroche.co/digital-marketing-case-studies/window-tinting-marketing-houston"),
            "approved_claims": ["10.63x Google Ads ROAS."],
        }
    ]
    result = validate_proof_claims("A similar account reached 10.63x Google Ads ROAS.", selected)
    assert result["valid"] is False
    assert result["errors"]


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
