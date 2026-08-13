"""Tests for the private local Upwork decision ledger."""
from pathlib import Path

import pytest

from upwork_mcp.ledger import bidding_report, record_outcome, record_screening


def _analysis(recommendation="strong_fit", proof="drd-law", boost="no_boost", bid=63):
    return {
        "recommendation": recommendation,
        "score": 82,
        "pricing": {"type": "hourly", "recommended_bid": bid},
        "case_studies": [{"key": proof}] if proof else [],
        "boost": {"recommendation": boost},
    }


def test_ledger_stores_minimal_decision_and_deduplicates_outcomes(tmp_path: Path):
    path = tmp_path / "private" / "ledger.sqlite3"
    job = {"url": "https://www.upwork.com/jobs/~abc123", "title": "Legal Google Ads"}

    recorded = record_screening(job, _analysis(), path=path)
    first = record_outcome(job["url"], "submitted", path=path)
    duplicate = record_outcome(job["url"], "submitted", path=path)

    assert recorded["job_key"] == "~abc123"
    assert first["recorded"] is True
    assert duplicate["recorded"] is False
    assert path.stat().st_mode & 0o777 == 0o600


def test_report_withholds_rates_until_minimum_sample(tmp_path: Path):
    path = tmp_path / "ledger.sqlite3"
    for index in range(4):
        url = f"https://www.upwork.com/jobs/~job{index}"
        record_screening({"url": url, "title": "Google Ads"}, _analysis(), path=path)
        record_outcome(url, "submitted", path=path)
        if index < 2:
            record_outcome(url, "viewed", path=path)

    report = bidding_report(path=path, minimum_sample=5)
    segment = report["segments"]["recommendation"][0]

    assert segment["submitted"] == 4
    assert segment["view_rate"] is None
    assert segment["sample_status"] == "need_1_more_submissions"
    assert report["policy_change"] == "none"


def test_report_calculates_view_interview_and_hire_rates(tmp_path: Path):
    path = tmp_path / "ledger.sqlite3"
    for index in range(5):
        url = f"https://www.upwork.com/jobs/~job{index}"
        record_screening({"url": url}, _analysis(), path=path)
        record_outcome(url, "submitted", path=path)
        if index < 4:
            record_outcome(url, "viewed", path=path)
        if index < 2:
            record_outcome(url, "interviewed", path=path)
        if index == 0:
            record_outcome(url, "hired", path=path)

    segment = bidding_report(path=path, minimum_sample=5)["segments"]["recommendation"][0]
    assert segment["view_rate"] == 0.8
    assert segment["interview_rate"] == 0.4
    assert segment["hire_rate"] == 0.2
    assert segment["sample_status"] == "usable"


def test_outcome_requires_a_screened_job(tmp_path: Path):
    with pytest.raises(ValueError, match="Screen the job"):
        record_outcome("https://www.upwork.com/jobs/~missing", "hired", path=tmp_path / "ledger.sqlite3")
