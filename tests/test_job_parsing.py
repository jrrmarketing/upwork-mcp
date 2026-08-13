"""Offline tests for normalising live Upwork job facts."""
import pytest
from pydantic import ValidationError

from upwork_mcp.tools.jobs import JobDetailsParams, JobSearchParams, parse_job_page_text

JOB_TEXT = """
Google Ads Expert for B2B SaaS
$60.00 - $70.00/hr
Hourly
More than 30 hrs/week
3 to 6 months
Expert
Proposals:
5 to 10
Last viewed by client:
2 hours ago
Interviewing:
1
Invites sent:
30
Unanswered invites:
27
No connects are required
Payment method verified
4.92 of 104 reviews
$204K+ total spent
89% hire rate, 4 open jobs
168 hires, 32 active
$24.70 /hr avg hourly rate paid
11,955 hours
Member since Jan 12, 2014
"""


def test_parse_job_page_text_captures_reachability_and_client_economics():
    result = parse_job_page_text(JOB_TEXT)

    assert result["job_type"] == "hourly"
    assert result["hourly_rate_min"] == 60
    assert result["hourly_rate_max"] == 70
    assert result["proposal_count"] == "5 to 10"
    assert result["interviewing"] == 1
    assert result["invites_sent"] == 30
    assert result["unanswered_invites"] == 27
    assert result["connects_required"] == 0
    assert result["client"] == {
        "payment_verified": True,
        "rating": 4.92,
        "total_reviews": 104,
        "hire_rate": 89.0,
        "open_jobs": 4,
        "total_hires": 168,
        "active_hires": 32,
        "avg_hourly_rate_paid": 24.7,
        "hours": 11955,
        "member_since": "Jan 12, 2014",
        "total_spent": 204000.0,
    }


def test_search_schema_rejects_silent_or_inverted_filters():
    with pytest.raises(ValidationError):
        JobSearchParams(query="Google Ads", budget_min=100, budget_max=50)

    with pytest.raises(ValidationError):
        JobSearchParams(query="Google Ads", unsupported_filter=True)


@pytest.mark.parametrize(
    "url",
    [
        "http://www.upwork.com/jobs/~abc",
        "https://example.com/jobs/~abc",
        "https://www.upwork.com/nx/messages/abc",
        "file:///etc/passwd",
    ],
)
def test_job_details_schema_rejects_non_job_navigation(url):
    with pytest.raises(ValidationError):
        JobDetailsParams(job_url=url)
