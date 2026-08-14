"""Offline integration tests for proposal preparation."""
import pytest

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
        "fee_net_text": ["Upwork service fee $6.30", "You'll receive $56.70 net"],
        "fee_net_status": "complete",
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
