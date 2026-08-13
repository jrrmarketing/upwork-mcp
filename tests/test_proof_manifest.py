"""Focused safety tests for the audited JRR proof manifest."""

from dataclasses import FrozenInstanceError

import pytest

from upwork_mcp.proof_manifest import (
    PROOF_BY_KEY,
    PROOF_MANIFEST,
    EvidenceStatus,
    ProofClaim,
    get_proof,
)


def _claim_texts(key: str) -> tuple[str, ...]:
    record = PROOF_BY_KEY[key]
    return tuple(claim.text for claim in record.permitted_claims)


def test_manifest_has_unique_complete_routing_records():
    assert len(PROOF_MANIFEST) == 10
    assert len(PROOF_BY_KEY) == len(PROOF_MANIFEST)
    assert tuple(PROOF_BY_KEY) == tuple(record.key for record in PROOF_MANIFEST)

    for record in PROOF_MANIFEST:
        assert record.key == record.key.strip().lower()
        assert record.vertical
        assert record.business_model
        assert record.services
        assert record.allowed_job_tags
        assert record.blocked_job_tags
        assert record.permitted_claims
        assert record.limitations
        assert record.current_url.startswith(
            "https://josiahroche.co/digital-marketing-case-studies/"
        )
        assert set(record.allowed_job_tags).isdisjoint(record.blocked_job_tags)
        assert record.claims is record.permitted_claims


def test_every_permitted_claim_has_period_source_and_status():
    allowed_statuses = {
        EvidenceStatus.VERIFIED,
        EvidenceStatus.ROUTE_ONLY_WITH_CAVEAT,
    }

    for record in PROOF_MANIFEST:
        for claim in record.permitted_claims:
            assert claim.text
            assert claim.period
            assert claim.source.startswith("jrr-marketing-website/")
            assert claim.status in allowed_statuses


def test_disputed_and_aggregate_claims_are_not_permitted():
    permitted = " ".join(
        claim.text.lower()
        for record in PROOF_MANIFEST
        for claim in record.permitted_claims
    )
    quarantined_fragments = (
        "$100m",
        "$53m",
        "81% of clients",
        "$63,000",
        "93 top",
        "211% more bookings",
        "47% lower cpl",
        "400% growth",
        "16x cheaper",
        "$79 cpl",
        "3,245%",
        "210% booking",
        "82% cpl",
        "86% cpl",
        "3x sales",
        "$58 cpl",
        "347% roas",
    )
    for fragment in quarantined_fragments:
        assert fragment not in permitted


def test_high_confidence_claims_match_the_audited_sources():
    assert _claim_texts("cage-and-miles-family-law") == (
        "$316.5k in tracked closed case revenue from Google Ads and SEO.",
        "586 tracked calls and forms.",
        "$137 tracked customer acquisition cost per case, including fees and ad spend.",
        "+295% tracked lifetime ROI.",
    )
    assert _claim_texts("melanson-ssdi-law") == (
        "Google Ads conversions increased from 10 to 51.",
        "Google Ads cost per conversion fell from $423.10 to $68.87.",
        "Google Ads spend fell from $4,231.02 to $3,512.46.",
    )
    assert _claim_texts("dark-shade-window-tinting") == (
        "10.63x Google Ads ROAS.",
        "$22.6k in ad-attributed revenue.",
        "51 Google Ads conversions.",
    )


def test_weak_or_conflicting_cases_expose_only_narrow_asset_claims():
    assert _claim_texts("japanese-head-spa") == (
        "844.11% actual ROAS.",
        "349 tracked leads.",
    )
    assert _claim_texts("exclusive-tents-b2b") == (
        "493 tracked form leads on the main site and 69 on the secondary site.",
    )
    assert _claim_texts("priority-one-plumbing") == (
        "1,258 tracked leads.",
        "33% tracked conversion rate.",
    )


def test_route_only_proof_is_explicitly_caveated():
    record = PROOF_BY_KEY["drd-criminal-law"]
    assert record.status is EvidenceStatus.ROUTE_ONLY_WITH_CAVEAT
    assert all(
        claim.status is EvidenceStatus.ROUTE_ONLY_WITH_CAVEAT
        for claim in record.permitted_claims
    )
    assert any("no result assets" in limitation.lower() for limitation in record.limitations)


def test_manifest_is_immutable_and_lookup_is_non_mutating():
    record = get_proof("  JAPANESE-HEAD-SPA  ")
    assert record is PROOF_BY_KEY["japanese-head-spa"]
    assert get_proof("missing") is None

    with pytest.raises(FrozenInstanceError):
        record.name = "Changed"  # type: ignore[misc]

    with pytest.raises(TypeError):
        PROOF_BY_KEY["new"] = record  # type: ignore[index]

    with pytest.raises(FrozenInstanceError):
        ProofClaim("claim", "period", "source").text = "changed"  # type: ignore[misc]
