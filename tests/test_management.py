"""Offline integration tests for proposal preparation."""
import pytest

from upwork_mcp.tools import management


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
        "form_status": "ready",
        "existing_proposal": False,
        "screening_questions": [],
        "duration_options": ["1 to 3 months"],
        "base_connects": 8,
        "fee_net_text": ["Upwork fee and net preview shown"],
        "boost_auction_text": ["Top bid 8 Connects"],
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
    assert result["exact_submission"]["screening_questions"] == []
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
