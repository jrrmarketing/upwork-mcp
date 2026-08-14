"""Offline integration tests for proposal preparation."""
import pytest
from pydantic import ValidationError

from upwork_mcp.tools import management, proposals


def _job():
    return {
        "url": "https://www.upwork.com/jobs/~law123",
        "title": "Google Ads audit for family law firm",
        "description": "Paid search lead quality and call tracking",
        "job_type": "hourly",
        "hourly_rate_min": 50,
        "hourly_rate_max": 90,
        "proposal_count": 5,
        "connects_required": 8,
        "client": {
            "payment_verified": True,
            "total_spent": 50_000,
            "total_hires": 10,
            "hire_rate": 80,
            "rating": 4.9,
            "avg_hourly_rate_paid": 70,
        },
    }


def _form():
    return {
        "job_url": "https://www.upwork.com/jobs/~law123",
        "job_id": "~law123",
        "form_url": "https://www.upwork.com/nx/proposals/job/~law123/apply",
        "job_title": "Google Ads audit for family law firm",
        "job_type": "hourly",
        "fixed_payment_structures": [],
        "form_status": "ready",
        "existing_proposal": False,
        "screening_questions": [],
        "screening_questions_status": "complete",
        "duration_options": [
            "Less than 1 month",
            "1 to 3 months",
            "3 to 6 months",
            "More than 6 months",
        ],
        "duration_options_status": "complete",
        "base_connects": 8,
        "base_connects_status": "complete",
        "fee_net_text": ["Upwork service fee $6.30", "You'll receive $56.70 net"],
        "fee_net_status": "complete",
        "fee_net_price_amount": "63.00",
        "fee_net_source": "scoped_reversible_price_preflight",
        "boost_auction_text": ["Top bid 8 Connects"],
        "boost_auction_status": "complete",
        "available_profile_highlights": ["Google Ads Search Certification"],
        "available_profile_highlights_status": "complete",
        "available_profile_highlights_details": {
            "chooser_opened": True,
            "chooser_dismissed": True,
        },
        "rate_increase_control_status": "complete",
        "external_action_taken": False,
    }


@pytest.mark.parametrize(
    ("updates", "error_fragment"),
    [
        ({"answers": ["  "]}, "answers"),
        ({"profile_highlights": ["  "]}, "highlights"),
        (
            {
                "profile_highlights": [
                    "Google Ads Search Certification",
                    " google ads search certification ",
                ]
            },
            "duplicates",
        ),
        ({"duration": "Whatever Upwork selected"}, "duration"),
    ],
)
def test_prepare_schema_rejects_blank_duplicate_or_commit_incompatible_lists(
    updates,
    error_fragment,
) -> None:
    values = {
        "job_url": "https://www.upwork.com/jobs/~law123",
        "cover_letter": "Exact approved copy",
        "rate": 63,
        "duration": "1 to 3 months",
        "profile_highlights": ["Google Ads Search Certification"],
    }
    values.update(updates)
    with pytest.raises(ValidationError, match=error_fragment):
        management.PrepareProposalParams(**values)


def test_prepare_schema_rejects_unsafe_price_configuration_and_multiple_milestones() -> None:
    common = {
        "job_url": "https://www.upwork.com/jobs/~law123",
        "cover_letter": "Exact approved copy",
        "duration": "1 to 3 months",
        "profile_highlights": ["Google Ads Search Certification"],
    }
    with pytest.raises(ValidationError, match="approved hourly rate"):
        management.PrepareProposalParams(**common, rate=49, minimum_hourly_rate=50)
    with pytest.raises(ValidationError, match="profile_hourly_rate"):
        management.PrepareProposalParams(
            **common,
            rate=50,
            profile_hourly_rate=49,
            minimum_hourly_rate=50,
        )
    with pytest.raises(ValidationError, match="at most 1 item"):
        management.PrepareProposalParams(
            **common,
            bid=500,
            payment_structure="by_milestone",
            milestones=[
                {"description": "Audit", "due_date": "2026-09-01", "amount": 250},
                {"description": "Review", "due_date": "2026-09-08", "amount": 250},
            ],
        )
    with pytest.raises(ValidationError, match="by_project terms only"):
        management.PrepareProposalParams(
            **common,
            bid=500,
            payment_structure="by_milestone",
            milestones=[
                {"description": "Audit", "due_date": "2026-09-01", "amount": 500},
            ],
        )


@pytest.fixture
def live_read_stubs(monkeypatch):
    async def fake_job(_params):
        return _job()

    async def fake_form(_params):
        return _form()

    monkeypatch.setattr(management, "get_job_details", fake_job)
    monkeypatch.setattr(management, "inspect_proposal_form", fake_form)


@pytest.mark.asyncio
async def test_prepare_proposal_binds_live_cost_questions_terms_and_copy(
    monkeypatch, tmp_path, live_read_stubs
):
    monkeypatch.setenv("UPWORK_MCP_STATE_DIR", str(tmp_path))
    result = await management.prepare_proposal(
        management.PrepareProposalParams(
            job_url="https://www.upwork.com/jobs/~law123",
            cover_letter=(
                "Hey, more than happy to take a look at this for you.\n\n"
                "I'd rather look properly before forming an opinion on the account."
            ),
            rate=63,
            duration="1 to 3 months",
            profile_highlights=["Google Ads Search Certification"],
        )
    )

    assert result["ready_for_owner_approval"] is True
    assert result["exact_submission"]["base_connects"] == 8
    assert result["exact_submission"]["job_id"] == "~law123"
    assert result["exact_submission"]["form_url"] == (
        "https://www.upwork.com/nx/proposals/job/~law123/apply"
    )
    assert result["exact_submission"]["job_title"] == "Google Ads audit for family law firm"
    assert result["exact_submission"]["job_type"] == "hourly"
    assert result["exact_submission"]["screening_questions"] == []
    assert result["exact_submission"]["screening_questions_status"] == "complete"
    assert result["exact_submission"]["duration_options_status"] == "complete"
    assert result["exact_submission"]["fee_net_text"] == [
        "Upwork service fee $6.30",
        "You'll receive $56.70 net",
    ]
    assert result["exact_submission"]["fee_net_status"] == "complete"
    assert result["exact_submission"]["fee_net_price_amount"] == "63.00"
    assert result["exact_submission"]["fee_net_source"] == (
        "scoped_reversible_price_preflight"
    )
    assert result["exact_submission"]["base_connects_status"] == "complete"
    assert result["exact_submission"]["boost_auction_status"] == "complete"
    assert result["exact_submission"]["rate_increase_control_status"] == "complete"
    committed_schema = proposals.SubmitProposalParams(
        action_id="uwa_schema_round_trip",
        **result["exact_submission"],
    )
    assert proposals.proposal_submission_payload(committed_schema) == result["exact_submission"]
    assert result["exact_submission"]["rate_increase_frequency"] == "Never"
    assert result["prepared_action"]["approved"] is False
    assert result["external_action_taken"] is False


@pytest.mark.asyncio
async def test_prepare_proposal_refuses_unaudited_aggregate_claim(
    monkeypatch, tmp_path, live_read_stubs
):
    monkeypatch.setenv("UPWORK_MCP_STATE_DIR", str(tmp_path))
    result = await management.prepare_proposal(
        management.PrepareProposalParams(
            job_url="https://www.upwork.com/jobs/~law123",
            cover_letter=(
                "Hey, more than happy to take a look at this for you.\n\n"
                "We've helped clients generate over $100M through Google Ads."
            ),
            rate=63,
            duration="1 to 3 months",
            profile_highlights=["Google Ads Search Certification"],
        )
    )

    assert result["ready_for_owner_approval"] is False
    assert result["prepared_action"] is None
    assert any("methodology" in error for error in result["errors"])


@pytest.mark.asyncio
async def test_prepare_proposal_refuses_unverified_connect_cost(monkeypatch, tmp_path, live_read_stubs):
    monkeypatch.setenv("UPWORK_MCP_STATE_DIR", str(tmp_path))

    async def unknown_cost(_params):
        return {**_form(), "base_connects": None}

    monkeypatch.setattr(management, "inspect_proposal_form", unknown_cost)
    result = await management.prepare_proposal(
        management.PrepareProposalParams(
            job_url="https://www.upwork.com/jobs/~law123",
            cover_letter="Hey, more than happy to take a look at this for you.\n\nHappy to review it properly.",
            rate=63,
            duration="1 to 3 months",
            profile_highlights=["Google Ads Search Certification"],
        )
    )
    assert result["ready_for_owner_approval"] is False
    assert any("Connect cost" in error for error in result["errors"])


@pytest.mark.asyncio
async def test_prepare_proposal_rejects_highlight_not_selectable_in_live_form(
    monkeypatch, tmp_path, live_read_stubs
):
    monkeypatch.setenv("UPWORK_MCP_STATE_DIR", str(tmp_path))

    result = await management.prepare_proposal(
        management.PrepareProposalParams(
            job_url="https://www.upwork.com/jobs/~law123",
            cover_letter="Hey, more than happy to take a look at this for you.\n\nHappy to review it properly.",
            rate=63,
            duration="1 to 3 months",
            profile_highlights=["Stale case-study title"],
        )
    )

    assert result["ready_for_owner_approval"] is False
    assert result["prepared_action"] is None
    assert any("not selectable in the live form" in error for error in result["errors"])


@pytest.mark.asyncio
async def test_prepare_proposal_fails_closed_when_highlight_enumeration_is_unavailable(
    monkeypatch, tmp_path, live_read_stubs
):
    monkeypatch.setenv("UPWORK_MCP_STATE_DIR", str(tmp_path))

    async def unavailable_highlights(_params):
        return {
            **_form(),
            "available_profile_highlights": [],
            "available_profile_highlights_status": "unavailable",
        }

    monkeypatch.setattr(management, "inspect_proposal_form", unavailable_highlights)
    result = await management.prepare_proposal(
        management.PrepareProposalParams(
            job_url="https://www.upwork.com/jobs/~law123",
            cover_letter="Hey, more than happy to take a look at this for you.\n\nHappy to review it properly.",
            rate=63,
            duration="1 to 3 months",
            profile_highlights=["Google Ads Search Certification"],
        )
    )

    assert result["ready_for_owner_approval"] is False
    assert result["prepared_action"] is None
    assert any("enumeration is not complete" in error for error in result["errors"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("form_update", "error_fragment"),
    [
        ({"screening_questions_status": "incomplete"}, "screening-question enumeration"),
        ({"duration_options_status": "unavailable"}, "duration-option enumeration"),
        ({"fee_net_status": "incomplete"}, "fee/net preview"),
        ({"rate_increase_control_status": "unavailable"}, "rate-increase control"),
    ],
)
async def test_prepare_proposal_refuses_incomplete_live_discovery(
    monkeypatch,
    tmp_path,
    form_update,
    error_fragment,
) -> None:
    async def fake_job(_params):
        return _job()

    async def fake_form(_params):
        return {**_form(), **form_update}

    monkeypatch.setenv("UPWORK_MCP_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(management, "get_job_details", fake_job)
    monkeypatch.setattr(management, "inspect_proposal_form", fake_form)
    result = await management.prepare_proposal(
        management.PrepareProposalParams(
            job_url="https://www.upwork.com/jobs/~law123",
            cover_letter="Hey, more than happy to look properly.\n\nI can review the account.",
            rate=63,
            duration="1 to 3 months",
            profile_highlights=["Google Ads Search Certification"],
        )
    )

    assert result["ready_for_owner_approval"] is False
    assert result["prepared_action"] is None
    assert any(error_fragment in error for error in result["errors"])


@pytest.mark.asyncio
async def test_prepare_proposal_requires_complete_live_auction_for_nonzero_boost(
    monkeypatch,
    tmp_path,
) -> None:
    async def fake_job(_params):
        return _job()

    async def fake_form(_params):
        return {
            **_form(),
            "boost_auction_text": ["Boost your proposal"],
            "boost_auction_status": "incomplete",
        }

    monkeypatch.setenv("UPWORK_MCP_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(management, "get_job_details", fake_job)
    monkeypatch.setattr(management, "inspect_proposal_form", fake_form)
    result = await management.prepare_proposal(
        management.PrepareProposalParams(
            job_url="https://www.upwork.com/jobs/~law123",
            cover_letter="Hey, more than happy to look properly.\n\nI can review the account.",
            rate=63,
            duration="1 to 3 months",
            profile_highlights=["Google Ads Search Certification"],
            boost_connects=5,
        )
    )

    assert result["ready_for_owner_approval"] is False
    assert result["prepared_action"] is None
    assert any("complete live boost-auction" in error for error in result["errors"])
    assert (
        "Positive-boost proposal preparation is disabled until the live Upwork flow can prove that the first Submit click is non-consequential."
        in result["errors"]
    )


@pytest.mark.asyncio
async def test_find_opportunities_canonicalizes_tracking_variants_before_hydration(monkeypatch) -> None:
    detail_calls: list[str] = []

    async def fake_search(params):
        return [
            {
                "url": f"https://www.upwork.com/jobs/~law123?source={params.search_mode}",
                "search_mode": params.search_mode,
            }
        ]

    async def fake_details(params):
        detail_calls.append(params.job_url)
        return _job()

    monkeypatch.setattr(management, "search_jobs", fake_search)
    monkeypatch.setattr(management, "get_job_details", fake_details)
    monkeypatch.setattr(
        management,
        "record_screening",
        lambda *_args, **_kwargs: {"recorded": True},
    )

    result = await management.find_opportunities("google ads", include_skips=True)

    assert result["unique_jobs_reviewed"] == 1
    assert result["invalid_job_urls_skipped"] == 0
    assert detail_calls == ["https://www.upwork.com/jobs/~law123"]
    assert result["opportunities"][0]["job"]["url"] == "https://www.upwork.com/jobs/~law123"
