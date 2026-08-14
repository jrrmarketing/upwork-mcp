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


@pytest.mark.parametrize(
    "description",
    [
        "A case-management system for family law firms.",
        "A practice-management suite for family law firms.",
        "A case intake dashboard for criminal defense attorneys.",
        "An AI chatbot for family law firms.",
        "A mobile solution for attorneys.",
        "An online booking system used by plumbing companies.",
        "A legal workflow suite for family law practices.",
        "A field-service management system for plumbers.",
        "A scheduling solution for plumbing companies.",
        "Our web-based case manager helps criminal defense lawyers.",
        "Our virtual receptionist serves family law firms.",
        "Our client intake bot supports criminal defense attorneys.",
        "We use an internal case-management system and then rent access to family law firms.",
        "Our internal dashboard is available for purchase by criminal defense lawyers.",
        "We use the bot in-house and let law firms pay for access.",
        "Our team uses the system internally and customers buy access.",
        "We built an internal tool that family law firms can buy.",
        "We use the app internally and monetize access for criminal defense attorneys.",
    ],
)
def test_product_and_mixed_model_counterexamples_never_borrow_service_proof(
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
            "Our family law firm offers divorce consultations.",
            "cage-and-miles-family-law",
        ),
        (
            "Google Ads for a criminal defense law firm",
            "We market our criminal defense services.",
            "drd-criminal-law",
        ),
        (
            "Google Ads for a criminal defense law firm",
            "We charge clients for legal representation.",
            "drd-criminal-law",
        ),
        (
            "Google Ads for a family law firm",
            "We are a legal services provider.",
            "cage-and-miles-family-law",
        ),
        (
            "Google Ads for a plumbing company",
            "Our plumbing company offers emergency repairs.",
            "priority-one-plumbing",
        ),
        (
            "Google Ads for a plumbing company",
            "We sell plumbing maintenance plans.",
            "priority-one-plumbing",
        ),
        (
            "Google Ads for a family law firm",
            "Our family law clients subscribe to our newsletter.",
            "cage-and-miles-family-law",
        ),
        (
            "Google Ads for a family law firm",
            "We provide access to an online client portal for existing clients.",
            "cage-and-miles-family-law",
        ),
        (
            "Google Ads for a family law firm",
            "We market our family law services and use Clio internally.",
            "cage-and-miles-family-law",
        ),
        (
            "Google Ads for a plumbing company",
            "We offer ServiceTitan access to our technicians.",
            "priority-one-plumbing",
        ),
        (
            "Google Ads for a family law firm",
            "We offer staff access to legal software internally.",
            "cage-and-miles-family-law",
        ),
        (
            "Google Ads for a criminal defense law firm",
            "Our firm charges flat legal fees and uses software in-house.",
            "drd-criminal-law",
        ),
    ],
)
def test_ordinary_service_commercial_language_keeps_exact_vertical_proof(
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


@pytest.mark.parametrize(
    ("expected", "description"),
    [
        ("excluded", "We require WhatConverts, not GTM."),
        ("excluded", "We need WhatConverts instead of GTM."),
        ("excluded", "The project needs WhatConverts rather than GTM."),
        ("excluded", "We need to use WhatConverts and avoid GTM."),
        ("excluded", "We require offline tracking without GTM."),
        ("excluded", "We need someone who can work without GTM."),
        ("excluded", "Do not install GTM."),
        ("excluded", "Never configure GTM."),
        ("excluded", "We will not manage GTM."),
        ("excluded", "No need to fix GTM."),
        ("excluded", "We need to skip GTM."),
        ("excluded", "We require the freelancer not to touch GTM."),
        ("excluded", "We need you not to use GTM."),
        ("excluded", "We need no GTM work."),
        ("excluded", "The project requires zero GTM changes."),
        ("excluded", "We need a part-time consultant, not full-time."),
        ("excluded", "We require part-time, not full-time."),
        ("excluded", "We need a consultant, not a full-time employee."),
        ("excluded", "We need you for 10 hours, not full-time."),
        ("required", "GTM setup is needed; reporting is optional."),
        ("required", "GTM work is mandatory; weekly calls are optional."),
        ("required", "GTM implementation must be completed; training is optional."),
        ("required", "GTM implementation is a requirement; reporting is optional."),
        ("required", "GTM knowledge is required, while certification is optional."),
        ("required", "GTM is needed; GA4 is optional."),
        ("required", "GTM is necessary; server-side tagging is optional."),
        ("required", "GTM is compulsory; Looker Studio is optional."),
        ("required", "GTM is a must-have; enhanced conversions are optional."),
        ("required", "GTM is non-negotiable; reporting is optional."),
        ("required", "We expect GTM implementation; reporting is optional."),
        ("required", "Please handle GTM; GA4 cleanup is optional."),
        ("required", "You'll own GTM; SEO is optional."),
        ("required", "GTM must be maintained; GA4 is optional."),
        ("required", "We are not looking for someone without GTM."),
        ("required", "We won't hire someone lacking GTM."),
        ("required", "GTM is optional for reporting, required for conversion tracking."),
        ("required", "GTM is optional initially, mandatory after launch."),
        ("required", "GTM isn't needed for reports, mandatory for conversions."),
        ("required", "We can work without GTM initially, but you'll configure it later."),
        ("required", "GTM may be omitted if WhatConverts is approved; otherwise it is mandatory."),
        ("ambiguous", "Some knowledge around GTM may come up."),
        ("ambiguous", "GTM could be relevant depending on the account."),
        ("ambiguous", "The client mentioned GTM in passing."),
        ("ambiguous", "We may revisit GTM later."),
        ("ambiguous", "GTM sits somewhere in the existing stack."),
    ],
)
def test_scope_classifier_counterexample_corpus(expected: str, description: str):
    result = analyze_job(
        {
            "title": "Google Ads consultant for a family law firm",
            "description": description,
            "job_type": "hourly",
            "hourly_rate_max": 100,
            "proposal_count": 3,
            "connects_required": 8,
            "client": _client(),
        }
    )

    if expected == "required":
        assert result.recommendation == "skip", description
        assert any("Tag Manager" in blocker for blocker in result.blockers), description
    elif expected == "ambiguous":
        assert result.recommendation == "scope_review", description
        assert result.blockers == [], description
        assert result.boost["recommendation"] == "no_boost", description
    else:
        assert not result.blockers, description
        assert result.recommendation not in {"skip", "scope_review"}, description


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


@pytest.mark.parametrize(
    ("expected", "description"),
    [
        ("excluded", "We require WhatConverts with zero GTM involvement."),
        ("excluded", "We need tracking that excludes GTM."),
        ("excluded", "We need a solution free from GTM."),
        ("excluded", "We require a non-GTM approach."),
        ("excluded", "We need no involvement with GTM."),
        ("excluded", "Please refrain from installing GTM."),
        ("excluded", "We ask that you not configure GTM."),
        ("excluded", "You must avoid installing GTM."),
        ("excluded", "WhatConverts is mandatory and GTM is disallowed."),
        ("excluded", "Use WhatConverts in place of GTM."),
        ("excluded", "The solution must be built without GTM."),
        ("excluded", "Absolutely no GTM."),
        ("excluded", "GTM is banned."),
        ("excluded", "GTM is not in scope."),
        ("excluded", "The freelancer won't handle GTM."),
        ("excluded", "GTM must not be touched."),
        ("excluded", "We need a contractor rather than a full-time hire."),
        ("excluded", "We require freelance support instead of full-time employment."),
        ("excluded", "We need ten hours weekly, never full-time."),
        ("excluded", "Full-time involvement is prohibited."),
        ("required", "GTM expertise is essential; certification is optional."),
        ("required", "GTM proficiency is required; GA4 is optional."),
        ("required", "GTM ownership is mandatory; reporting is optional."),
        ("required", "GTM maintenance is required; calls are optional."),
        ("required", "GTM deployment is necessary; training is optional."),
        ("required", "GTM must be supported; reporting is optional."),
        ("required", "GTM has to be maintained; calls are optional."),
        ("required", "GTM, required for conversion tracking, is central; reporting is optional."),
        ("required", "GTM (mandatory for Ads) is needed; reporting is optional."),
        ("required", "We won't hire anyone unfamiliar with GTM."),
        ("required", "We are not considering candidates inexperienced in GTM."),
        ("required", "Applicants new to GTM should not apply."),
        ("required", "GTM novices need not apply."),
        ("required", "Only GTM-qualified applicants will be considered."),
        ("required", "Candidates must possess GTM expertise."),
        ("required", "We do not want applicants unfamiliar with GTM."),
        ("required", "We don't need anyone inexperienced in GTM."),
        ("required", "We will not hire a GTM novice."),
        ("required", "GTM can be omitted only when approved; otherwise configure it."),
        ("required", "GTM is optional if WhatConverts works; failing that, implement it."),
        ("required", "You may skip GTM for the audit; the implementation phase requires it."),
        ("ambiguous", "GTM familiarity may become useful as the project evolves."),
        ("ambiguous", "The account contains a GTM container, but ownership is undecided."),
        ("ambiguous", "GTM could be included after discovery."),
        ("ambiguous", "Whether GTM is part of phase two is still open."),
        ("ambiguous", "The client has not decided who will own GTM."),
        ("ambiguous", "GTM is mentioned in the brief without a defined deliverable."),
        ("ambiguous", "GTM access may be available if needed."),
        ("ambiguous", "The weekly commitment may increase toward full-time."),
        ("ambiguous", "This could become a full-time engagement depending on results."),
    ],
)
def test_scope_classifier_extended_counterexample_corpus(expected: str, description: str):
    result = analyze_job(
        {
            "title": "Google Ads consultant for a family law firm",
            "description": description,
            "job_type": "hourly",
            "hourly_rate_max": 100,
            "proposal_count": 3,
            "connects_required": 8,
            "client": _client(),
        }
    )

    employee_block = any("employee-style" in blocker for blocker in result.blockers)
    gtm_block = any("Tag Manager" in blocker for blocker in result.blockers)
    if expected == "required":
        assert result.recommendation == "skip", description
        assert gtm_block or employee_block, description
    elif expected == "ambiguous":
        assert result.recommendation == "scope_review", description
        assert not gtm_block and not employee_block, description
        assert result.boost["recommendation"] == "no_boost", description
    else:
        assert not gtm_block and not employee_block, description
        assert result.recommendation not in {"skip", "scope_review"}, description


@pytest.mark.parametrize(
    ("title", "description", "expected_key"),
    [
        ("Google Ads consultant", "Our family law firm has a client portal and needs Google Ads lead generation.", "cage-and-miles-family-law"),
        ("Google Ads consultant", "Our family law firm uses a CRM and needs Google Ads lead generation.", "cage-and-miles-family-law"),
        ("Google Ads consultant", "Our criminal defense attorneys log into Clio and need Google Ads lead generation.", "drd-criminal-law"),
        ("Google Ads consultant", "Our plumbers use a scheduling system and need Google Ads lead generation.", "priority-one-plumbing"),
        ("Google Ads consultant", "Our plumbing company runs ServiceTitan and needs Google Ads lead generation.", "priority-one-plumbing"),
        ("Google Ads consultant", "Our family law firm adopted a client portal and needs Google Ads lead generation.", "cage-and-miles-family-law"),
        ("Google Ads consultant", "The family law team works in a case-management system and needs Google Ads lead generation.", "cage-and-miles-family-law"),
        ("Google Ads consultant", "Our attorneys rely on a legal dashboard and need Google Ads lead generation for the family law practice.", "cage-and-miles-family-law"),
        ("Google Ads consultant", "Our family law firm uses legal software and needs Google Ads lead generation.", "cage-and-miles-family-law"),
        ("Google Ads consultant", "Our family law firm offers a suite of legal services and needs Google Ads lead generation.", "cage-and-miles-family-law"),
        ("Google Ads consultant", "Our plumbing company offers a system of maintenance plans and needs Google Ads lead generation.", "priority-one-plumbing"),
        ("Google Ads consultant", "Our attorneys use a dashboard to monitor Google Ads for the family law firm.", "cage-and-miles-family-law"),
        ("Google Ads consultant", "Please submit an application for this Google Ads role at our family law firm.", "cage-and-miles-family-law"),
        ("Google Ads consultant", "Our family law firm needs a system for managing Google Ads.", "cage-and-miles-family-law"),
        ("Google Ads consultant", "We need Google Ads for our family law firm, and our CRM stores leads.", "cage-and-miles-family-law"),
    ],
)
def test_extended_internal_tool_language_retains_service_vertical_proof(
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

    assert result.case_studies[0]["key"] == expected_key, description
    assert result.case_studies[0]["match_strength"] == "exact", description
    assert result.boost["recommendation"] == "inspect_live_auction", description


@pytest.mark.parametrize(
    "description",
    [
        "We sell an AI copilot to family law firms and need Google Ads lead generation.",
        "We offer an AI agent to criminal defense attorneys and need Google Ads.",
        "We license a WordPress plugin to plumbing companies and need Google Ads.",
        "We sell a browser extension to family law attorneys and need paid search.",
        "We provide an API to family law firms and need Google Ads.",
        "We offer a case-management database to law firms and need Google Ads.",
        "We sell a practice-management toolkit to attorneys and need paid search.",
        "We market a digital workspace to criminal defense firms and need Google Ads.",
        "We sell a legal operations hub to family law practices and need PPC.",
        "We offer a lead-routing engine to plumbing companies and need Google Ads.",
        "We use the AI copilot internally and sell it to family law firms. We need Google Ads.",
        "We use the plugin internally and offer it to plumbers. We need Google Ads.",
        "Our internal API is licensed to family law firms. We need Google Ads.",
        "We built the database internally and family law firms subscribe to it. We need paid search.",
        "Our staff use the tool internally and family law firms pay monthly. We need Google Ads.",
        "We built an internal platform with paid subscriptions for law firms. We need Google Ads.",
        "We are a marketing agency serving family law firms and need Google Ads for our agency.",
        "We sell outsourced bookkeeping to family law firms and need paid search.",
        "We are a consultancy serving criminal defense firms and need Google Ads.",
        "We provide call answering services to plumbing companies and need Google Ads.",
        "We sell coaching programs to family law attorneys and need Google Ads.",
        "We publish a newsletter for criminal defense lawyers and need PPC.",
        "We run a directory for family law firms and need paid search.",
        "We sell training courses to plumbing companies and need Google Ads.",
    ],
)
def test_extended_marketed_models_never_borrow_end_client_vertical_proof(description: str):
    result = analyze_job(
        {
            "title": "Google Ads consultant",
            "description": description,
            "job_type": "hourly",
            "hourly_rate_max": 100,
            "proposal_count": 3,
            "connects_required": 8,
            "client": _client(),
        }
    )

    assert not any(study["match_strength"] == "exact" for study in result.case_studies), description
    assert result.boost["recommendation"] == "no_boost", description


@pytest.mark.parametrize(
    "job_update",
    [
        {"hours_per_week": "More than 30 hrs/week"},
        {"contract_to_hire": True},
        {"hours_per_week": "More than 30 hrs/week", "contract_to_hire": True},
        {"description": "Google Ads lead generation. More than 30 hrs/week."},
    ],
)
def test_structured_employee_style_scope_is_never_bid_or_boosted(job_update):
    job = {
        "title": "Google Ads for a family law firm",
        "description": "Google Ads lead generation.",
        "job_type": "hourly",
        "hourly_rate_max": 100,
        "proposal_count": 3,
        "connects_required": 8,
        "client": _client(),
    }
    job.update(job_update)
    result = analyze_job(job)

    assert result.recommendation == "skip"
    assert any("employee-style" in blocker for blocker in result.blockers)
    assert result.boost["recommendation"] == "no_boost"


def test_inverted_hourly_range_requires_manual_review_and_never_boosts():
    result = analyze_job(
        {
            "title": "Google Ads for a family law firm",
            "description": "Google Ads lead generation.",
            "job_type": "hourly",
            "hourly_rate_min": 200,
            "hourly_rate_max": 100,
            "proposal_count": 3,
            "connects_required": 8,
            "client": _client(),
        }
    )

    assert result.pricing["position"] == "invalid_client_range"
    assert result.recommendation == "scope_review"
    assert result.boost["recommendation"] == "no_boost"

    partial = analyze_job(
        {
            "title": "Google Ads for a family law firm",
            "description": "Google Ads lead generation.",
            "job_type": "hourly",
            "hourly_rate_min": 120,
            "proposal_count": 3,
            "connects_required": 8,
            "client": _client(),
        }
    )
    assert partial.pricing["position"] == "partial_client_range"
    assert partial.pricing["recommended_bid"] >= 120
    assert partial.recommendation == "scope_review"
    assert partial.boost["recommendation"] == "no_boost"


@pytest.mark.parametrize(
    ("budget_update", "expected_position"),
    [
        ({"budget_min": 2000, "budget_max": 1000}, "invalid_client_budget"),
        ({"budget_min": -100, "budget_max": 1000}, "invalid_client_budget"),
        ({"budget_min": 1000}, "partial_client_budget"),
    ],
)
def test_invalid_or_partial_fixed_budget_requires_manual_review_and_never_boosts(
    budget_update,
    expected_position: str,
):
    job = {
        "title": "Google Ads for a family law firm",
        "description": "Google Ads lead generation.",
        "job_type": "fixed",
        "proposal_count": 3,
        "connects_required": 8,
        "client": _client(),
        **budget_update,
    }
    result = analyze_job(job, PricingContext(minimum_fixed_fee=500))

    assert result.pricing["position"] == expected_position
    assert result.recommendation == "scope_review"
    assert result.boost["recommendation"] == "no_boost"


def test_explicitly_rejected_vertical_proof_is_not_selected():
    result = analyze_job(
        {
            "title": "Google Ads consultant",
            "description": (
                "Plumbing case studies are irrelevant; our criminal defense firm needs Google Ads."
            ),
            "job_type": "hourly",
            "hourly_rate_max": 100,
            "proposal_count": 3,
            "client": _client(),
        }
    )

    assert result.case_studies[0]["key"] == "drd-criminal-law"
    assert all(study["key"] != "priority-one-plumbing" for study in result.case_studies)

    rejected = analyze_job(
        {
            "title": "Google Ads consultant",
            "description": "Do not use family law case studies in the proposal.",
            "job_type": "hourly",
            "hourly_rate_max": 100,
            "proposal_count": 3,
            "client": _client(),
        }
    )
    assert all(study["key"] != "cage-and-miles-family-law" for study in rejected.case_studies)


@pytest.mark.parametrize(
    "description",
    [
        "Do not apply if you plan to install GTM.",
        "We need a solution that bypasses GTM.",
        "We require tracking independent of GTM.",
        "We need an implementation agnostic to GTM.",
        "We need WhatConverts alone; GTM cannot be involved.",
        "We need a GTM-free tracking solution.",
        "We need a GTM-independent implementation.",
        "We require GTM-less attribution.",
        "We need tracking outside GTM.",
        "We need to bypass GTM.",
        "We need to steer clear of GTM.",
        "We require an approach that leaves GTM untouched.",
        "We need Google Ads, independent of GTM.",
    ],
)
def test_gtm_free_scope_language_is_never_misread_as_required(description: str):
    result = analyze_job(
        {
            "title": "Google Ads for a family law firm",
            "description": description,
            "job_type": "hourly",
            "hourly_rate_max": 100,
            "proposal_count": 3,
            "connects_required": 8,
            "client": _client(),
        }
    )
    assert not any("Tag Manager" in blocker for blocker in result.blockers), description
    assert result.recommendation != "skip", description


@pytest.mark.parametrize(
    "description",
    [
        "We need WhatConverts and no contact with GTM.",
        "We need WhatConverts with no interaction with Google Tag Manager.",
        "We require PPC; GTM remains out of bounds.",
        "We require PPC; GTM stays off-limits.",
        "GTM is optional now, with compulsory implementation of GA4 after approval.",
    ],
)
def test_explicit_gtm_contact_and_boundary_exclusions_are_never_hard_scope(
    description: str,
):
    result = analyze_job(
        {
            "title": "Google Ads for a family law firm",
            "description": description,
            "job_type": "hourly",
            "hourly_rate_max": 100,
            "proposal_count": 3,
            "connects_required": 8,
            "client": _client(),
        }
    )

    assert not any("Tag Manager" in blocker for blocker in result.blockers), description
    assert result.recommendation not in {"skip", "scope_review"}, description


@pytest.mark.parametrize(
    "description",
    [
        "We need contact with GTM.",
        "GTM remains mandatory.",
    ],
)
def test_gtm_exclusion_lookalikes_remain_required(description: str):
    result = analyze_job(
        {
            "title": "Google Ads for a family law firm",
            "description": description,
            "job_type": "hourly",
            "hourly_rate_max": 100,
            "proposal_count": 3,
            "connects_required": 8,
            "client": _client(),
        }
    )

    assert result.recommendation == "skip", description
    assert any("Tag Manager" in blocker for blocker in result.blockers), description


@pytest.mark.parametrize(
    "description",
    [
        "GTM is optional? No, it is mandatory.",
        "GTM used to be optional. It is now required.",
        "GTM was optional during discovery. It becomes mandatory at implementation.",
        "GTM isn't required for phase one. Phase two requires it.",
        "GTM is optional today. We need it tomorrow.",
        "GTM is optional only during discovery; implementation is compulsory.",
        "GTM can be skipped until launch; after launch you own it.",
        "GTM isn't needed for reports. It's indispensable for conversion tracking.",
        "GTM isn’t needed for reports. It’s indispensable for conversion tracking.",
        "GTM is optional for now, with compulsory implementation after sign-off.",
        "GTM is optional at first; later you are responsible for it.",
        "GTM is optional at first; later you will be responsible for it.",
        "We need WhatConverts and no contact with GTM; after sign-off it is mandatory.",
        "We require PPC; GTM remains out of bounds for reporting, but implementation is mandatory.",
    ],
)
def test_later_gtm_requirement_overrides_earlier_phase_exclusion(description: str):
    result = analyze_job(
        {
            "title": "Google Ads for a family law firm",
            "description": description,
            "job_type": "hourly",
            "hourly_rate_max": 100,
            "proposal_count": 3,
            "connects_required": 8,
            "client": _client(),
        }
    )
    assert result.recommendation == "skip", description
    assert any("Tag Manager" in blocker for blocker in result.blockers), description


@pytest.mark.parametrize(
    "description",
    [
        "GTM is optional at first; later you may be responsible for it.",
        "GTM is optional at first; later you might be responsible for it.",
        "GTM is optional for now; later ownership is undecided.",
        "GTM is optional now; later you are responsible for it if the client requests it.",
        "GTM is optional now, potentially with compulsory implementation after approval.",
        "GTM is optional today, but may become required after discovery.",
    ],
)
def test_uncertain_future_gtm_ownership_requires_scope_review(description: str):
    result = analyze_job(
        {
            "title": "Google Ads for a family law firm",
            "description": description,
            "job_type": "hourly",
            "hourly_rate_max": 100,
            "proposal_count": 3,
            "connects_required": 8,
            "client": _client(),
        }
    )

    assert result.recommendation == "scope_review", description
    assert not any("Tag Manager" in blocker for blocker in result.blockers), description
    assert result.boost["recommendation"] == "no_boost", description
    assert result.boost["max_extra_connects"] == 0, description


@pytest.mark.parametrize(
    "description",
    [
        "This is freelance at first, then a permanent employee role.",
        "This is a permanent salaried position.",
        "You will join our team as an employee.",
        "The successful freelancer transitions to permanent employment.",
        "This is a 30+ hours/week role.",
        "We require at least 30 hours per week.",
        "This is a contract to hire position.",
        "This is a temp-to-perm opportunity.",
        "This starts part-time and becomes a staff role after onboarding.",
        "Full-time is not expected initially. The position transitions to it after launch.",
    ],
)
def test_employee_commitment_aliases_are_never_bid_or_boosted(description: str):
    result = analyze_job(
        {
            "title": "Google Ads for a family law firm",
            "description": description,
            "job_type": "hourly",
            "hourly_rate_max": 100,
            "proposal_count": 3,
            "connects_required": 8,
            "client": _client(),
        }
    )
    assert result.recommendation == "skip", description
    assert any("employee-style" in blocker for blocker in result.blockers), description
    assert result.boost["recommendation"] == "no_boost", description


@pytest.mark.parametrize(
    "description",
    [
        "Google Ads and LSA campaigns.",
        "Google Ads and LSAs.",
        "Google Ads and Local Service Ads.",
        "Google Ads and Google Local Services.",
        "Google Ads and Google Guaranteed campaigns.",
        "Google Ads and LSA expertise.",
        "Google Ads and Local Services advertising.",
        "Google Ads and Local Services campaigns.",
        "Google Ads and App campaigns.",
        "Google Ads and UAC management.",
        "Google Ads and mobile app installs.",
        "Google Ads and app promotion campaigns.",
        "Google Ads and Universal App Campaigns.",
        "Google Ads acquisition for our iOS app.",
    ],
)
def test_lsa_and_app_aliases_are_hard_scope_blocks(description: str):
    result = analyze_job(
        {
            "title": "Google Ads for a family law firm",
            "description": description,
            "job_type": "hourly",
            "hourly_rate_max": 100,
            "proposal_count": 3,
            "client": _client(),
        }
    )
    assert result.recommendation == "skip", description
    assert result.blockers, description


@pytest.mark.parametrize(
    "alias",
    [
        "paid social",
        "FB Ads",
        "Meta advertising",
        "Facebook advertising",
        "Instagram advertising",
        "LinkedIn PPC",
        "TikTok advertising",
        "Reddit advertising",
        "social media advertising",
    ],
)
def test_unsupported_channel_aliases_add_boundary_and_disable_boost(alias: str):
    result = analyze_job(
        {
            "title": "Google Ads for a family law firm",
            "description": f"Manage Google Ads and {alias}.",
            "job_type": "hourly",
            "hourly_rate_max": 100,
            "proposal_count": 3,
            "connects_required": 8,
            "client": _client(),
        }
    )
    assert any("unsupported channels" in boundary for boundary in result.scope_boundaries), alias
    assert result.boost["recommendation"] == "no_boost", alias


@pytest.mark.parametrize(
    ("title", "description"),
    [
        ("Google Ads for AI copilot for family law firms", "Paid search."),
        ("AI agent serving criminal defense attorneys", "Google Ads."),
        ("WordPress plugin for plumbing companies", "Google Ads."),
        ("browser extension for family law attorneys", "Paid search."),
        ("API used by family law firms", "Google Ads."),
        ("legal operations hub for family law practices", "PPC."),
        ("lead-routing engine for plumbing companies", "Google Ads."),
        ("call answering service for plumbers", "Google Ads."),
        ("Google Ads consultant", "We sell an app to plumbing businesses."),
        ("Google Ads consultant", "We provide a platform to cabinet makers."),
        ("Google Ads consultant", "We are a PPC agency serving family law firms."),
        ("Google Ads consultant", "We are an SEO agency serving plumbing companies."),
        ("Google Ads consultant", "We sell accounting services to family law firms."),
        ("Google Ads consultant", "We are a managed-IT service serving criminal defense firms."),
        ("Google Ads consultant", "We sell an AI voice receptionist service to law firms."),
    ],
)
def test_product_and_vertical_facing_service_titles_never_borrow_client_proof(
    title: str,
    description: str,
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
    assert not any(study["match_strength"] == "exact" for study in result.case_studies), title
    assert result.boost["recommendation"] == "no_boost", title


@pytest.mark.parametrize(
    ("title", "description", "expected_key"),
    [
        ("Google Ads consultant", "Our family law firm is developing software for its lawyers.", "cage-and-miles-family-law"),
        ("Google Ads consultant", "Our plumbing company is building software for its plumbers.", "priority-one-plumbing"),
        ("Google Ads consultant", "Our family law firm commissioned an app for our lawyers.", "cage-and-miles-family-law"),
        ("Google Ads consultant", "Our family law firm maintains a dashboard for its attorneys.", "cage-and-miles-family-law"),
        ("Google Ads consultant", "We use a CRM to support our family law firm.", "cage-and-miles-family-law"),
        ("Google Ads consultant", "Our plumbing company commissioned an app for its plumbers.", "priority-one-plumbing"),
        ("Google Ads consultant", "Our criminal defense firm maintains a portal for its attorneys.", "drd-criminal-law"),
        ("Google Ads consultant", "Our family law firm hired a marketing agency serving law firms.", "cage-and-miles-family-law"),
        ("Google Ads consultant", "Our plumbing company uses a call answering service for plumbers.", "priority-one-plumbing"),
        ("Google Ads consultant", "Our criminal defense firm subscribes to a newsletter for lawyers.", "drd-criminal-law"),
        ("Google Ads consultant", "Our family law firm is listed in a directory for attorneys.", "cage-and-miles-family-law"),
        ("Google Ads consultant", "Our plumbing team attends a training course for plumbers.", "priority-one-plumbing"),
    ],
)
def test_internal_tools_and_consumed_vendors_keep_current_vertical_proof(
    title: str,
    description: str,
    expected_key: str,
):
    result = analyze_job(
        {
            "title": title,
            "description": f"{description} We need Google Ads lead generation.",
            "job_type": "hourly",
            "hourly_rate_max": 100,
            "proposal_count": 3,
            "connects_required": 8,
            "client": _client(),
        }
    )
    assert result.case_studies[0]["key"] == expected_key, description
    assert result.case_studies[0]["match_strength"] == "exact", description


@pytest.mark.parametrize(
    "description",
    [
        "No Google Ads or SEO; paid social only.",
        "This project excludes SEO and Google Ads; it is for email marketing.",
        "Google Ads is outside scope. We need social media management.",
        "We need neither Google Ads nor SEO; only organic social.",
    ],
)
def test_explicitly_excluded_core_services_never_create_fit_or_boost(description: str):
    result = analyze_job(
        {
            "title": "Marketing consultant for a family law firm",
            "description": description,
            "job_type": "hourly",
            "hourly_rate_max": 100,
            "proposal_count": 3,
            "connects_required": 8,
            "client": _client(),
        }
    )
    assert result.recommendation == "skip", description
    assert result.boost["recommendation"] == "no_boost", description
    assert any("core Google Ads or SEO" in blocker for blocker in result.blockers), description


@pytest.mark.parametrize(
    ("description", "excluded_key"),
    [
        ("We are not a family law firm; we are an accounting firm.", "cage-and-miles-family-law"),
        ("Family law is outside our market.", "cage-and-miles-family-law"),
        ("We have no family law clients.", "cage-and-miles-family-law"),
        ("Nothing to do with criminal defense.", "drd-criminal-law"),
        ("Criminal defense is not our field.", "drd-criminal-law"),
        ("Unlike plumbers, we sell direct-to-consumer furniture.", "priority-one-plumbing"),
        ("Every legal vertical except family law.", "cage-and-miles-family-law"),
        ("Other than family law, we serve accountants.", "cage-and-miles-family-law"),
        ("Rather than plumbers, our customers are cabinet makers.", "priority-one-plumbing"),
        ("We no longer serve family law.", "cage-and-miles-family-law"),
        ("We stopped working with criminal defense clients.", "drd-criminal-law"),
        ("We previously served plumbers but now sell furniture.", "priority-one-plumbing"),
        ("Family law was our old niche.", "cage-and-miles-family-law"),
        ("Plumbing is a former market, not our current one.", "priority-one-plumbing"),
    ],
)
def test_negated_or_historical_verticals_never_supply_current_proof(
    description: str,
    excluded_key: str,
):
    result = analyze_job(
        {
            "title": "Google Ads consultant",
            "description": f"{description} We need Google Ads.",
            "job_type": "hourly",
            "hourly_rate_max": 100,
            "proposal_count": 3,
            "client": _client(),
        }
    )
    assert all(study["key"] != excluded_key for study in result.case_studies), description


def test_explicitly_excluded_proof_limitations_do_not_disqualify_current_vertical():
    for description in (
        "Our family law firm needs Google Ads. We do not use Local Services Ads.",
        "Our family law firm needs Google Ads. No SEO content creation is required.",
        "Our family law firm needs Google Ads. SEO only is not the scope.",
    ):
        result = analyze_job(
            {
                "title": "Google Ads consultant",
                "description": description,
                "job_type": "hourly",
                "hourly_rate_max": 100,
                "proposal_count": 3,
                "client": _client(),
            }
        )
        assert result.case_studies[0]["key"] == "cage-and-miles-family-law", description


@pytest.mark.parametrize(
    "description",
    [
        "Our online store sells tools to plumbers.",
        "We sell uniforms to plumbing companies online.",
        "We operate a Shopify store for family law attorneys.",
        "We are a DTC brand selling products to criminal defense lawyers.",
    ],
)
def test_commerce_selling_to_a_vertical_never_borrows_service_business_proof(
    description: str,
):
    result = analyze_job(
        {
            "title": "Google Ads consultant",
            "description": f"{description} We need paid search.",
            "job_type": "hourly",
            "hourly_rate_max": 100,
            "proposal_count": 3,
            "connects_required": 8,
            "client": _client(),
        }
    )
    assert not any(study["match_strength"] == "exact" for study in result.case_studies), description
    assert result.boost["recommendation"] == "no_boost", description


@pytest.mark.parametrize(
    "description",
    [
        "Google Search Ads management.",
        "SEM specialist.",
        "Search advertising management.",
        "Google advertising campaign management.",
        "Paid media management focused on Google Search.",
        "Google Search campaign optimisation.",
        "Search engine marketing.",
        "Google Search specialist.",
    ],
)
def test_common_google_ads_aliases_are_recognised_as_core_service_fit(description: str):
    result = analyze_job(
        {
            "title": "Paid acquisition for a family law firm",
            "description": description,
            "job_type": "hourly",
            "hourly_rate_max": 100,
            "proposal_count": 3,
            "connects_required": 8,
            "client": _client(),
        }
    )
    assert not any("core Google Ads or SEO" in blocker for blocker in result.blockers), description
    assert result.recommendation != "skip", description


@pytest.mark.parametrize(
    ("title", "description", "expected_key"),
    [
        ("Google Ads consultant", "Submit your online application for our family law firm's Google Ads role.", "cage-and-miles-family-law"),
        ("Google Ads consultant", "Please send an online application to our family law firm.", "cage-and-miles-family-law"),
        ("Google Ads consultant", "This web application is for candidates applying to manage Google Ads for our family law firm.", "cage-and-miles-family-law"),
        ("Google Ads consultant", "Use the online application portal for our criminal defense firm Google Ads opening.", "drd-criminal-law"),
    ],
)
def test_hiring_application_language_is_not_mistaken_for_a_product_model(
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
    assert result.case_studies[0]["key"] == expected_key, description
    assert result.case_studies[0]["match_strength"] == "exact", description
