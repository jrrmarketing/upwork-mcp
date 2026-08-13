"""Private local decision ledger for evidence-based Upwork bidding reports.

The decision/outcome tables intentionally store no proposal copy, message bodies,
client names, or browser content. They record the smallest set of facts needed to
measure whether screening, proof, pricing, and boost choices are working.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

POLICY_VERSION = "jrr-upwork-v1"
OUTCOMES = frozenset({"submitted", "viewed", "interviewed", "hired", "declined", "closed", "withdrawn"})


def default_ledger_path() -> Path:
    state_dir = Path(os.environ.get("UPWORK_MCP_STATE_DIR", "~/.upwork-mcp")).expanduser()
    return state_dir / "ledger.sqlite3"


def _connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = (path or default_ledger_path()).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        db_path.parent.chmod(0o700)
    except OSError:
        pass
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE IF NOT EXISTS screenings (
            job_key TEXT PRIMARY KEY,
            job_url TEXT NOT NULL,
            job_title TEXT,
            first_seen_at TEXT NOT NULL,
            last_screened_at TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            recommendation TEXT NOT NULL,
            score INTEGER NOT NULL,
            price_type TEXT,
            recommended_bid REAL,
            proof_keys_json TEXT NOT NULL,
            boost_recommendation TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_key TEXT NOT NULL,
            outcome TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            source TEXT NOT NULL,
            UNIQUE(job_key, outcome),
            FOREIGN KEY(job_key) REFERENCES screenings(job_key) ON DELETE CASCADE
        );
        """
    )
    try:
        db_path.chmod(0o600)
    except OSError:
        pass
    return connection


def job_key(job_url: str) -> str:
    match = re.search(r"~[A-Za-z0-9]+", job_url)
    if match:
        return match.group(0)
    return hashlib.sha256(job_url.encode("utf-8")).hexdigest()[:24]


def record_screening(
    job: Mapping[str, Any],
    analysis: Mapping[str, Any],
    *,
    path: Path | None = None,
    policy_version: str = POLICY_VERSION,
) -> dict[str, Any]:
    """Upsert one normalised screening decision without storing private copy."""
    job_url = str(job.get("url") or "").strip()
    if not job_url:
        raise ValueError("A canonical job URL is required to record a screening")
    key = job_key(job_url)
    now = datetime.now(UTC).isoformat()
    pricing_value = analysis.get("pricing")
    boost_value = analysis.get("boost")
    studies_value = analysis.get("case_studies")
    pricing: Mapping[str, Any] = pricing_value if isinstance(pricing_value, Mapping) else {}
    boost: Mapping[str, Any] = boost_value if isinstance(boost_value, Mapping) else {}
    studies: list[Any] = studies_value if isinstance(studies_value, list) else []
    proof_keys = [str(item.get("key")) for item in studies if isinstance(item, Mapping) and item.get("key")]

    with _connect(path) as connection:
        connection.execute(
            """
            INSERT INTO screenings (
                job_key, job_url, job_title, first_seen_at, last_screened_at,
                policy_version, recommendation, score, price_type,
                recommended_bid, proof_keys_json, boost_recommendation
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_key) DO UPDATE SET
                job_url = excluded.job_url,
                job_title = excluded.job_title,
                last_screened_at = excluded.last_screened_at,
                policy_version = excluded.policy_version,
                recommendation = excluded.recommendation,
                score = excluded.score,
                price_type = excluded.price_type,
                recommended_bid = excluded.recommended_bid,
                proof_keys_json = excluded.proof_keys_json,
                boost_recommendation = excluded.boost_recommendation
            """,
            (
                key,
                job_url,
                str(job.get("title") or "")[:300],
                now,
                now,
                policy_version,
                str(analysis.get("recommendation") or "unknown"),
                int(analysis.get("score") or 0),
                pricing.get("type"),
                pricing.get("recommended_bid"),
                json.dumps(proof_keys),
                str(boost.get("recommendation") or "unknown"),
            ),
        )
    return {"recorded": True, "job_key": key, "policy_version": policy_version}


def record_outcome(
    job_url: str,
    outcome: str,
    *,
    path: Path | None = None,
    source: str = "owner_system_readback",
    occurred_at: datetime | None = None,
) -> dict[str, Any]:
    """Record a verified Upwork outcome for a job already in the ledger."""
    normalized = outcome.strip().lower()
    if normalized not in OUTCOMES:
        raise ValueError(f"outcome must be one of: {', '.join(sorted(OUTCOMES))}")
    key = job_key(job_url)
    timestamp = (occurred_at or datetime.now(UTC)).astimezone(UTC).isoformat()
    with _connect(path) as connection:
        exists = connection.execute("SELECT 1 FROM screenings WHERE job_key = ?", (key,)).fetchone()
        if not exists:
            raise ValueError("Screen the job before recording an outcome")
        cursor = connection.execute(
            "INSERT OR IGNORE INTO outcomes (job_key, outcome, occurred_at, source) VALUES (?, ?, ?, ?)",
            (key, normalized, timestamp, source),
        )
    return {"recorded": cursor.rowcount == 1, "job_key": key, "outcome": normalized}


def _price_band(price_type: str | None, amount: float | None) -> str:
    prefix = price_type or "unknown"
    if amount is None:
        return f"{prefix}:unpriced"
    if amount <= 50:
        band = "50_or_less"
    elif amount <= 63:
        band = "51_to_63"
    elif amount <= 100:
        band = "64_to_100"
    else:
        band = "over_100"
    return f"{prefix}:{band}"


def _summarise(groups: Mapping[str, list[tuple[sqlite3.Row, set[str]]]], minimum_sample: int) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for label, items in sorted(groups.items()):
        submitted = sum(bool(outcomes & {"submitted", "viewed", "interviewed", "hired"}) for _, outcomes in items)
        viewed = sum(bool(outcomes & {"viewed", "interviewed", "hired"}) for _, outcomes in items)
        interviewed = sum(bool(outcomes & {"interviewed", "hired"}) for _, outcomes in items)
        hired = sum("hired" in outcomes for _, outcomes in items)
        enough = submitted >= minimum_sample
        output.append(
            {
                "segment": label,
                "screened": len(items),
                "submitted": submitted,
                "viewed": viewed,
                "interviewed": interviewed,
                "hired": hired,
                "view_rate": round(viewed / submitted, 3) if enough else None,
                "interview_rate": round(interviewed / submitted, 3) if enough else None,
                "hire_rate": round(hired / submitted, 3) if enough else None,
                "sample_status": "usable" if enough else f"need_{max(0, minimum_sample - submitted)}_more_submissions",
            }
        )
    return output


def bidding_report(*, path: Path | None = None, minimum_sample: int = 5) -> dict[str, Any]:
    """Report outcomes without automatically changing policy weights."""
    if minimum_sample < 3:
        raise ValueError("minimum_sample must be at least 3")
    with _connect(path) as connection:
        screens = connection.execute("SELECT * FROM screenings ORDER BY last_screened_at DESC").fetchall()
        outcome_rows = connection.execute("SELECT job_key, outcome FROM outcomes").fetchall()
    outcomes_by_job: dict[str, set[str]] = {}
    for row in outcome_rows:
        outcomes_by_job.setdefault(row["job_key"], set()).add(row["outcome"])

    paired = [(row, outcomes_by_job.get(row["job_key"], set())) for row in screens]
    dimensions: dict[str, dict[str, list[tuple[sqlite3.Row, set[str]]]]] = {
        "recommendation": {},
        "price_band": {},
        "proof": {},
        "boost": {},
    }
    for row, outcomes in paired:
        dimensions["recommendation"].setdefault(row["recommendation"], []).append((row, outcomes))
        dimensions["price_band"].setdefault(_price_band(row["price_type"], row["recommended_bid"]), []).append((row, outcomes))
        dimensions["boost"].setdefault(row["boost_recommendation"], []).append((row, outcomes))
        proof_keys: Iterable[str] = json.loads(row["proof_keys_json"] or "[]") or ["no_verified_proof"]
        for proof_key in proof_keys:
            dimensions["proof"].setdefault(proof_key, []).append((row, outcomes))

    return {
        "screened_jobs": len(screens),
        "jobs_with_outcomes": sum(bool(outcomes) for _, outcomes in paired),
        "minimum_submission_sample": minimum_sample,
        "segments": {name: _summarise(groups, minimum_sample) for name, groups in dimensions.items()},
        "policy_change": "none",
        "note": "This report is descriptive. It never changes screening or bid weights automatically.",
    }
