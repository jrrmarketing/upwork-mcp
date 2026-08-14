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


@pytest.mark.parametrize(
    "title",
    [
        "Google Ads for a law school",
        "Paid search for legal SaaS software",
        "Google Ads for law-enforcement recruiting",
        "Shopping ads for a legal-document ecommerce store",
    ],
)
def test_non_law_firm_business_models_never_receive_exact_law_firm_proof(title: str):
    result = analyze_job(
        {
            "title": title,
            "description": "Google Ads account management and paid search lead generation",
            "job_type": "hourly",
            "hourly_rate_max": 100,
            "proposal_count": 3,
            "connects_required": 8,
            "client": _client(),
        }
    )

    assert not any(
        study["match_strength"] == "exact" and "law" in study["key"]
        for study in result.case_studies
    )
    assert result.boost["recommendation"] == "no_boost"


@pytest.mark.parametrize(
    "description",
    [
        "Paid search for an attorney practice management platform sold to law firms.",
        "Paid search for a lawyer recruitment marketplace serving law firms.",
        "Paid search for a criminal defense software platform.",
        "Legal SaaS for law firms whose customers use Clio software internally.",
        "We sell a subscription product that helps criminal defense lawyers manage cases.",
    ],
)
def test_business_model_mismatch_in_description_cannot_supply_exact_law_proof(
    description: str,
):
    result = analyze_job(
        {
            "title": "Google Ads specialist",
            "description": description,
            "job_type": "hourly",
            "hourly_rate_max": 100,
            "proposal_count": 3,
            "connects_required": 8,
            "client": _client(),
        }
    )

    assert not any(study["match_strength"] == "exact" for study in result.case_studies)
    assert result.boost["recommendation"] == "no_boost"


@pytest.mark.parametrize(
    ("title", "description", "expected_key"),
    [
        (
            "Google Ads for a family law firm",
            "Paid search lead generation. We use Clio software internally.",
            "cage-and-miles-family-law",
        ),
        (
            "Google Ads for a criminal defense law firm",
            "Lead generation for the firm using practice-management software internally.",
            "drd-criminal-law",
        ),
        (
            "Google Ads for a plumbing company",
            "Local plumbing leads. Our team uses ServiceTitan software internally.",
            "priority-one-plumbing",
        ),
        (
            "Google Ads for a family law firm",
            "The firm relies on practice-management software for internal case work.",
            "cage-and-miles-family-law",
        ),
        (
            "Google Ads for a plumbing company",
            "Our internal booking flow connects to proprietary dispatch software.",
            "priority-one-plumbing",
        ),
    ],
)
def test_internal_software_mentions_do_not_erase_genuine_vertical_proof(
    title: str,
    description: str,
    expected_key: str,
):
    result = analyze_job(
        {
            "title": title,
            "description": description,
            "job_type": "hourly",
            "hourly_rate_max": 100,
            "proposal_count": 3,
            "connects_required": 8,
            "client": _client(),
        }
    )

    assert result.case_studies[0]["key"] == expected_key
    assert result.case_studies[0]["match_strength"] == "exact"
    assert result.boost["recommendation"] == "inspect_live_auction"


@pytest.mark.parametrize(
    "description",
    [
        "Recruit new family-law clients through paid search.",
        "The partners sometimes teach at the local law school.",
        "The firm uses proprietary case software internally.",
        "The family-law firm uses proprietary case software internally.",
        "The plumbing company relies on proprietary dispatch software internally.",
    ],
)
def test_incidental_model_words_do_not_erase_real_client_service_proof(description: str):
    plumbing = "plumbing" in description
    result = analyze_job(
        {
            "title": (
                "Google Ads for a plumbing company"
                if plumbing
                else "Google Ads for a family law firm"
            ),
            "description": description,
            "job_type": "hourly",
            "hourly_rate_max": 100,
            "proposal_count": 3,
            "connects_required": 8,
            "client": _client(),
        }
    )

    expected_key = "priority-one-plumbing" if plumbing else "cage-and-miles-family-law"
    assert result.case_studies[0]["key"] == expected_key
    assert result.case_studies[0]["match_strength"] == "exact"
    assert result.boost["recommendation"] == "inspect_live_auction"


def test_internal_software_use_does_not_hide_a_separate_licensed_product_model():
    result = analyze_job(
        {
            "title": "Google Ads specialist",
            "description": (
                "We use software internally and sell licenses to criminal-defense law firms."
            ),
            "job_type": "hourly",
            "hourly_rate_max": 100,
            "proposal_count": 3,
            "connects_required": 8,
            "client": _client(),
        }
    )

    assert not any(study["match_strength"] == "exact" for study in result.case_studies)
    assert result.boost["recommendation"] == "no_boost"


@pytest.mark.parametrize(
    "description",
    [
        "Our platform serves family law firms.",
        "We built software for criminal defense law firms.",
        "A platform for divorce attorneys.",
        "Software used by family law firms.",
        "We provide case management software to lawyers.",
        "Our software helps family law firms.",
        "Software for family law firms.",
        "Our app helps criminal defense lawyers.",
        "We sell an AI tool to criminal defense attorneys.",
        "Our CRM helps family law firms.",
        "We market a case management solution to family law firms.",
        "We offer an AI assistant to family law firms.",
        "We run an app that connects criminal defense attorneys with leads.",
        "We help lawyers manage cases through our web application.",
        "Our online application helps family law firms.",
        "We are a legal technology provider for family law firms.",
        "We created a portal for criminal defense attorneys.",
        "We provide an AI assistant for plumbers.",
        "We make workflow automation for family law firms.",
        "Our family law CRM solution is sold by subscription.",
        "We developed a case-management solution for criminal defense firms.",
        "We're a software vendor helping plumbers.",
        "Our cloud service helps family law practices manage matters.",
        "We offer an online product to criminal defense lawyers.",
    ],
)
def test_marketed_software_models_never_borrow_law_firm_proof(description: str):
    result = analyze_job(
        {
            "title": "Google Ads specialist",
            "description": description,
            "job_type": "hourly",
            "hourly_rate_max": 100,
            "proposal_count": 3,
            "connects_required": 8,
            "client": _client(),
        }
    )

    assert not any(study["match_strength"] == "exact" for study in result.case_studies)
    assert result.boost["recommendation"] == "no_boost"


def test_legal_practice_software_used_internally_does_not_erase_law_firm_proof():
    result = analyze_job(
        {
            "title": "Google Ads for a criminal defense law firm",
            "description": "We use legal practice management software internally.",
            "job_type": "hourly",
            "hourly_rate_max": 100,
            "proposal_count": 3,
            "connects_required": 8,
            "client": _client(),
        }
    )

    assert result.case_studies[0]["key"] == "drd-criminal-law"
    assert result.case_studies[0]["match_strength"] == "exact"
    assert result.boost["recommendation"] == "inspect_live_auction"


@pytest.mark.parametrize(
    "description",
    [
        "Our software platform is used by our staff internally.",
        "We license legal software for internal use.",
        "We implemented legal practice management software in-house.",
        "Our ServiceTitan platform is used by our technicians internally.",
        "We subscribe to legal software internally.",
        "Our legal practice-management platform is for internal operations.",
        "We integrate with a legal software platform for tracking.",
        "We are migrating to legal practice management software.",
        "Our attorneys work in legal practice management software.",
        "We purchase legal practice management software for our team.",
        "We adopted legal practice management software internally.",
        "We run our cases through legal practice management software.",
        "Legal software powers our internal casework.",
        "We have legal practice management software for internal operations.",
        "Our internal CRM helps our family law team.",
        "We use an internal CRM that helps our family law team.",
        "Our internal software helps our family law team.",
        "The software we use internally helps our criminal defense attorneys.",
        "We built an internal app for our attorneys.",
        "We license legal software to our staff for internal use.",
        "We provide legal software to our internal team.",
        "We operate legal software internally.",
        "We developed legal software for our own firm.",
    ],
)
def test_explicit_internal_product_language_preserves_real_service_proof(description: str):
    plumbing = "servicetitan" in description.casefold()
    family = "family law" in description.casefold() and "criminal defense" not in description.casefold()
    result = analyze_job(
        {
            "title": (
                "Google Ads for a plumbing company"
                if plumbing
                else (
                    "Google Ads for a family law firm"
                    if family
                    else "Google Ads for a criminal defense law firm"
                )
            ),
            "description": description,
            "job_type": "hourly",
            "hourly_rate_max": 100,
            "proposal_count": 3,
            "connects_required": 8,
            "client": _client(),
        }
    )

    expected_key = (
        "priority-one-plumbing"
        if plumbing
        else ("cage-and-miles-family-law" if family else "drd-criminal-law")
    )
    assert result.case_studies[0]["key"] == expected_key
    assert result.case_studies[0]["match_strength"] == "exact"
    assert result.boost["recommendation"] == "inspect_live_auction"


@pytest.mark.parametrize(
    "description",
    [
        "We built the platform for our internal team and now offer it to family law firms.",
        "We use the platform internally and provide access to criminal defense law firms.",
        "We license software internally and offer subscriptions to family law firms.",
        "Our staff use the software internally and family law clients subscribe to it.",
        "The app is used by our team and offered commercially to criminal defense firms.",
        "We use our CRM internally and charge family law firms to access it.",
    ],
)
def test_mixed_internal_and_marketed_product_models_never_borrow_service_proof(
    description: str,
):
    result = analyze_job(
        {
            "title": "Google Ads specialist",
            "description": description,
            "job_type": "hourly",
            "hourly_rate_max": 100,
            "proposal_count": 3,
            "connects_required": 8,
            "client": _client(),
        }
    )

    assert not any(study["match_strength"] == "exact" for study in result.case_studies)
    assert result.boost["recommendation"] == "no_boost"


def test_specific_plumbing_proof_outranks_generic_home_services_proof():
    result = analyze_job(
        {
            "title": "Google Ads for a plumbing company",
            "description": "Paid search lead generation for local plumbing calls",
            "job_type": "hourly",
            "hourly_rate_max": 100,
            "proposal_count": 3,
            "client": _client(),
        }
    )

    assert result.case_studies[0]["key"] == "priority-one-plumbing"
    assert result.case_studies[0]["match_strength"] == "exact"


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


def test_ambiguous_unsupported_scope_requires_manual_review_and_never_boosts():
    result = analyze_job(
        {
            "title": "Google Ads audit for family law firm",
            "description": (
                "Paid search lead generation. Familiarity with GTM would be useful."
            ),
            "job_type": "hourly",
            "hourly_rate_max": 100,
            "proposal_count": 3,
            "connects_required": 8,
            "client": _client(),
        }
    )

    assert result.recommendation == "scope_review"
    assert result.blockers == []
    assert result.proposal_plan["requires_scope_review"] is True
    assert any(
        "Manual scope review required" in boundary
        for boundary in result.scope_boundaries
    )
    assert result.boost["recommendation"] == "no_boost"
    assert result.boost["max_extra_connects"] == 0


def test_explicit_scope_exclusions_do_not_trigger_hard_skips():
    for description in (
        "Do not use GTM; use WhatConverts for offline conversion outcomes.",
        "GTM isn't required; use WhatConverts instead.",
        "We won't use Google Tag Manager for this account.",
        "There is no need for GTM in this engagement.",
        "GTM not required; use WhatConverts instead.",
        "GTM, which is not required, can be ignored.",
        "GTM won't be necessary.",
        "GTM is specifically not required.",
        "GTM is explicitly excluded.",
        "GTM should not be used.",
        "GTM must not be used.",
        "GTM is outside the scope.",
        "GTM excluded from this engagement.",
        "GTM isn't required.",
        "GTM isn’t required.",
        "No requirement for GTM.",
        "There is no requirement to use GTM.",
        "We have no plans to use GTM.",
        "Please avoid GTM and use WhatConverts.",
        "We are not using GTM for this account.",
        "We aren't using GTM.",
        "We aren’t using GTM.",
        "We are not planning to use GTM.",
        "We don't plan to use GTM.",
        "We don't want to use GTM.",
        "We will not be using Google Tag Manager.",
        "This is not a full-time role; the consultant will work five hours a week.",
        "This isn't a full-time role.",
        "Full-time support is unnecessary.",
        "We aren't hiring full-time.",
        "We do not need you to use GTM.",
        "GTM cannot be used.",
        "You do not have to use GTM.",
        "We don't need someone full-time.",
        "Full-time hours are not expected.",
        "This will not be a full-time position.",
        "Part-time rather than full-time support.",
        "We don't need a full-time person; this is five hours a week.",
        "We are not looking for full-time support.",
        "GTM is out of scope.",
        "Skip GTM.",
        "Omit GTM.",
        "Use WhatConverts instead of GTM.",
        "This is part-time instead of full-time.",
        "Candidates who can't use GTM may still apply.",
        "We won't reject candidates without GTM experience.",
        "GTM is preferred but not required.",
        "Either GTM or WhatConverts can be used.",
        "No full-time commitment is required.",
        "Full-time is preferred but part-time is acceptable.",
        "Applicants lacking GTM are still eligible.",
        "GTM is not mandatory.",
        "GTM experience is not a prerequisite.",
        "No GTM experience is necessary.",
        "Applicants lacking GTM will still be considered.",
        "You can use GTM, but WhatConverts is acceptable instead.",
        "We can work without GTM.",
        "This is 20 hours, not full-time.",
        "Either GTM or WhatConverts is acceptable.",
        "GTM would be ideal, but WhatConverts is fine.",
        "GTM can be left out.",
        "GTM does not form part of this engagement.",
        "This is fractional, not full-time.",
        "No one needs GTM to apply.",
        "GTM doesn't need to be used.",
        "GTM is preferred, but we're open to WhatConverts.",
        "Applicants need not know GTM.",
        "GTM may be omitted.",
        "GTM can be skipped.",
        "GTM is not part of this job.",
        "GTM is prohibited.",
        "GTM is forbidden.",
        "GTM is a nice-to-have, not a must-have.",
        "Do not touch GTM.",
        "Don't make any GTM changes.",
        "You are not responsible for GTM.",
        "GTM isn't your responsibility.",
        "Either GTM or WhatConverts works for us.",
        "GTM or WhatConverts, your choice.",
        "You can choose between GTM and WhatConverts.",
        "WhatConverts is an acceptable substitute for GTM.",
        "WhatConverts can replace GTM.",
        "We are open to GTM or WhatConverts.",
        "We don't mind whether you use GTM or WhatConverts.",
        "GTM experience isn't a dealbreaker.",
        "Lack of GTM experience won't disqualify applicants.",
        "Candidates are welcome without GTM.",
        "Anyone can apply, with or without GTM.",
        "We accept applicants regardless of GTM experience.",
        "GTM knowledge does not affect eligibility.",
        "No GTM experience? You can still apply.",
        "GTM experience is a bonus.",
        "This role is freelance, not full-time.",
        "Contract basis, not full-time.",
        "Part-time only; no full-time work.",
        "No full-time requirement.",
        "There is no expectation of full-time availability.",
        "Full-time isn't the only option; part-time works too.",
        "Full-time is off the table.",
        "This will never become full-time.",
        "We cannot offer full-time.",
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


@pytest.mark.parametrize(
    "description",
    [
        "Do not apply if you cannot use GTM.",
        "Candidates without GTM experience will not be considered.",
        "We won't hire anyone without Google Tag Manager experience.",
        "Without GTM experience, you cannot apply.",
        "You cannot apply without GTM experience.",
        "If you can't use GTM, don't apply.",
        "Applicants who cannot use GTM should not apply.",
        "No one without GTM experience should apply.",
        "Applicants without GTM knowledge are ineligible.",
        "We cannot run these ads without GTM.",
        "GTM is not optional.",
        "GTM cannot be omitted.",
        "GTM is not only required but central to the brief.",
        "GTM is required and not currently configured.",
        "We need GTM because conversions are not tracked.",
        "Our GTM is broken and not sending conversions.",
        "Not optional: GTM is required.",
        "GTM isn't needed for analytics, and GTM is required for Ads.",
        "Candidates without GTM must not apply.",
        "Applications without GTM will be rejected.",
        "Do not apply unless you can use GTM.",
        "We have no GTM and need you to install it.",
        "GTM isn't configured and must be set up.",
        "We don't use GTM yet and need you to implement it.",
        "GTM isn't needed for reports, although it is mandatory for conversions.",
        "Anyone without GTM may not apply.",
        "Don't skip GTM.",
        "GTM must never be omitted.",
        "Never leave out GTM.",
        "GTM is optional for reporting but required for conversion tracking.",
        "No GTM currently; setup is needed.",
        "We do not use GTM yet; implementation will be required.",
        "We do not have GTM, so please install it.",
        "We do not use GTM; please implement it.",
        "We don't currently have GTM. Please install it.",
        "GTM is not out of scope.",
        "GTM is non-optional.",
        "We don't want candidates who can't use GTM.",
        "We don't need someone who can't use GTM.",
        "We don't use GTM yet because we need it implemented.",
        "We aren't using GTM now; this needs to be configured.",
        "We don't use GTM and expect you to install it.",
        "We're not using GTM yet. Please add it.",
        "We don't use GTM, so add it.",
        "We aren't using GTM because you will implement it.",
        "We are not using GTM yet and would like you to configure it.",
        "We aren't using GTM until you configure it.",
        "GTM is not excluded.",
        "GTM is never optional.",
        "GTM is by no means optional.",
        "GTM is hardly optional.",
        "GTM is anything but optional.",
        "GTM isn't merely optional.",
        "GTM is no longer optional.",
        "GTM is not just optional.",
        "GTM cannot be considered optional.",
        "GTM is compulsory rather than optional.",
        "GTM is a must, not optional.",
    ],
)
def test_negative_eligibility_language_still_proves_gtm_is_required(description: str):
    result = analyze_job(
        {
            "title": "Google Ads specialist",
            "description": description,
            "job_type": "hourly",
            "hourly_rate_max": 80,
            "proposal_count": 5,
            "client": _client(),
        }
    )

    assert result.recommendation == "skip"
    assert any("Tag Manager" in blocker for blocker in result.blockers)


def test_negation_does_not_leak_across_but_into_a_required_scope():
    result = analyze_job(
        {
            "title": "Google Ads consultant",
            "description": (
                "We do not require web development, but require Google Tag Manager implementation."
            ),
            "job_type": "hourly",
            "hourly_rate_max": 80,
            "proposal_count": 5,
            "client": _client(),
        }
    )

    assert result.recommendation == "skip"
    assert any("Tag Manager" in blocker for blocker in result.blockers)


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


def test_client_range_below_floor_is_skipped_and_never_boosted():
    result = analyze_job(
        {
            "title": "Google Ads audit for criminal defense law firm",
            "description": "Paid search lead generation and account review",
            "job_type": "hourly",
            "hourly_rate_min": 10,
            "hourly_rate_max": 30,
            "proposal_count": 3,
            "connects_required": 8,
            "client": _client(),
        }
    )

    assert result.pricing["position"] == "above_client_range"
    assert result.recommendation == "skip"
    assert result.boost["recommendation"] == "no_boost"


def test_inconsistent_client_rate_range_is_never_inverted():
    result = analyze_job(
        {
            "title": "Google Ads audit for law firm",
            "description": "Paid search lead generation",
            "job_type": "hourly",
            "hourly_rate_min": 100,
            "hourly_rate_max": 60,
            "proposal_count": 5,
            "client": _client(),
        }
    )

    low, high = result.pricing["defensible_range"]
    assert low <= high
    assert any("minimum exceeded" in item for item in result.pricing["assumptions"])


@pytest.mark.parametrize("external_url", ["bit.ly/call", "example.dev/path"])
def test_proposal_copy_rejects_bare_external_urls_with_any_normal_tld(external_url: str):
    result = validate_upwork_copy(f"I can review the account. Details: {external_url}")

    assert result["valid"] is False
    assert any("external URL" in error for error in result["errors"])


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
    with pytest.raises(ValueError, match=r"owner-approved \$50 floor"):
        PricingContext(profile_hourly_rate=49, minimum_hourly_rate=50)

    with pytest.raises(ValueError, match=r"owner-approved \$50 floor"):
        PricingContext(profile_hourly_rate=63, minimum_hourly_rate=49)


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
