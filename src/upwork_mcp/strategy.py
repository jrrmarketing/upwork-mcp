"""Deterministic JRR decision support for Upwork opportunities.

This module deliberately contains no browser automation and performs no writes.  It
turns live Upwork facts into a recommendation that can be unit tested before those
facts are used in a proposal workflow.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from .proof_manifest import PROOF_MANIFEST, EvidenceStatus, ProofRecord

SEARCH_CERTIFICATION = "Google Ads Search Certification"

SERVICE_TERMS: Mapping[str, tuple[str, ...]] = {
    "google_ads": ("google ads", "adwords", "ppc", "paid search", "pmax", "performance max", "shopping"),
    "paid_search": ("google ads", "adwords", "ppc", "paid search"),
    "paid_search_audit": ("audit", "review", "account analysis"),
    "paid_search_restructure": ("restructure", "campaign structure", "account structure", "audit"),
    "account_restructure": ("restructure", "campaign structure", "account structure"),
    "performance_max": ("pmax", "performance max"),
    "performance_max_audit": ("pmax", "performance max", "audit"),
    "shopping": ("shopping", "merchant center", "product feed"),
    "shopping_feed_segmentation": ("shopping", "merchant center", "product feed", "feed segmentation"),
    "seo": ("seo", "organic search", "search engine optimization"),
    "local_seo": ("local seo", "google business profile", "organic search"),
    "landing_pages": (
        "landing page",
        "unbounce",
        "cro",
        "conversion rate optimisation",
        "conversion rate optimization",
    ),
    "website_cro": ("website", "landing page", "cro", "conversion rate"),
    "conversion_tracking": (
        "conversion tracking",
        "offline conversion",
        "attribution",
        "call tracking",
        "whatconverts",
    ),
    "whatconverts_attribution": ("whatconverts", "offline conversion", "lead attribution", "call tracking"),
    "revenue_attribution": ("revenue attribution", "conversion value", "roas", "tracked revenue"),
    "call_tracking": ("call tracking", "phone calls", "whatconverts"),
    "lead_tracking": ("lead tracking", "call tracking", "form tracking", "whatconverts"),
    "lead_attribution": ("lead attribution", "offline conversion", "whatconverts"),
    "local_paid_search": ("local", "google ads", "paid search", "ppc"),
    "local_lead_generation": ("local", "lead generation", "calls", "bookings"),
    "geo_targeting": ("geo targeting", "location targeting", "geographic"),
    "audience_targeting": ("audience", "age targeting", "demographic"),
    "brand_protection": ("brand campaign", "brand protection", "branded search"),
    "team_handover": ("team", "training", "handover"),
}

TAG_TERMS: Mapping[str, tuple[str, ...]] = {
    "legal": ("legal services", "legal practice", "attorney", "lawyer"),
    "law_firm": ("law firm", "attorney", "lawyer"),
    "family_law": ("family law", "divorce", "custody"),
    "criminal_defense": ("criminal defense", "criminal defence", "dui", "dwi"),
    "ssdi": ("ssdi", "social security disability"),
    "disability_law": ("disability law", "disability attorney", "ssdi"),
    "ecommerce": ("ecommerce", "e-commerce", "shopify", "woocommerce", "shopping"),
    "furniture": ("furniture", "cushion", "home decor"),
    "home_services": ("home service", "plumber", "plumbing", "hvac", "roofing", "contractor", "window tint"),
    "plumbing": ("plumber", "plumbing"),
    "trades": ("trades", "plumber", "plumbing", "hvac", "roofing", "contractor", "cabinet"),
    "home_improvement": ("home improvement", "remodel", "cabinet", "contractor"),
    "window_tinting": ("window tint", "architectural tint", "solar film"),
    "beauty": ("beauty", "aesthetic", "cosmetic", "med spa", "head spa"),
    "wellness": ("wellness", "spa"),
    "spa": ("spa", "med spa"),
    "med_spa_adjacent": ("med spa", "aesthetic", "cosmetic", "beauty clinic", "private surgery"),
    "healthcare": ("healthcare", "medical", "clinic", "private surgery"),
    "private_clinic": ("private clinic", "clinic", "private healthcare"),
    "medical_marketing": ("medical marketing", "healthcare marketing", "clinic"),
    "b2b": ("b2b", "business to business"),
    "manufacturing": ("manufacturing", "manufacturer", "industrial"),
    "industrial": ("industrial", "manufacturing", "manufacturer"),
    "high_ticket": ("high ticket", "high-ticket", "capital equipment", "large contract"),
    "local_business": ("local business", "local service"),
    "cabinet_maker": ("cabinet", "cabinetry", "joinery"),
}

# These tags describe a broad market or business-model adjacency, not the
# study's exact vertical. They can support a consultative analogy, but never the
# "exact proof" threshold used to recommend a Connects boost.
ADJACENT_PROOF_TAGS = frozenset(
    {
        "legal",
        "ecommerce",
        "home_services",
        "trades",
        "home_improvement",
        "beauty",
        "wellness",
        "med_spa_adjacent",
        "healthcare",
        "b2b",
        "high_ticket",
        "local_business",
    }
)

# A vertical word in a job for software, education, recruitment, or law
# enforcement does not describe the same business model as a client-services
# case study. Match explicit marketed models rather than broad nouns: a law firm
# may recruit clients, mention a law school, or use software internally without
# becoming a recruiting, education, or software business.
NON_CLIENT_TITLE_MODEL_PATTERN = re.compile(
    r"\b(?:saas|legal[- ]tech|law[- ]tech|law[- ]school|law[- ]enforcement|"
    r"(?:software|subscription|mobile[- ]app)\s+(?:company|product|platform|business)|"
    r"(?:recruiting|recruitment|staffing)\s+(?:agency|company|firm|marketplace|platform|software)|"
    r"(?:legal|lawyer|attorney|criminal[- ]defen[cs]e|family[- ]law)[- ]+"
    r"(?:software|platform|marketplace|app))\b",
    re.I,
)
NON_CLIENT_DESCRIPTION_MODEL_PATTERN = re.compile(
    r"(?:"
    r"\b(?:saas|legal[- ]tech|law[- ]tech|subscription\s+(?:product|service|business))\b|"
    r"\b(?:attorney|lawyer|legal|law[- ]firm|criminal[- ]defen[cs]e|family[- ]law)\s+"
    r"(?:(?:practice[- ]management|recruitment)\s+)?"
    r"(?:saas|software|platform|marketplace|mobile[- ]app)\b|"
    r"\bpractice[- ]management\s+(?:software|platform|system|tool)\b"
    r"[^.;!?]{0,80}\b(?:sold|selling|serv(?:e|es|ing)|market(?:ed|ing)?|for\s+sale)\b|"
    r"\b(?:sell|sells|selling|sold|license|licenses|licensing|offer|offers|offering)\b"
    r"[^.;!?]{0,90}\b(?:licenses?|subscriptions?|saas|software|platform|marketplace|"
    r"mobile[- ]app|digital\s+product)\b|"
    r"\b(?:google\s+ads|adwords|paid\s+search|ppc|marketing)\b[^.;!?]{0,45}\bfor\b"
    r"[^.;!?]{0,65}\b(?:law[- ]school|university|college|law[- ]enforcement|police|"
    r"recruiting\s+(?:marketplace|platform|agency)|software\s+(?:company|platform|product))\b"
    r")",
    re.I,
)


HARD_SCOPE_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        r"\b(?:google tag manager|gtm|server[- ]side tag(?:ging)?)\b",
        "Google Tag Manager implementation is outside JRR scope",
    ),
    (r"\b(?:local services ads?|google lsa|lsa management)\b", "Local Services Ads management is outside JRR scope"),
    (r"\b(?:appsflyer|mobile app campaign|app install campaign)\b", "App campaign tracking is outside JRR scope"),
)

UNSUPPORTED_CHANNELS = {
    "meta": ("meta ads", "facebook ads", "instagram ads"),
    "linkedin": ("linkedin ads", "linkedin campaign manager"),
    "reddit": ("reddit ads",),
    "tiktok": ("tiktok ads",),
    "capterra": ("capterra",),
}

SALESY_PHRASES = (
    "i'm an expert",
    "i am an expert",
    "i'm highly experienced",
    "i am highly experienced",
    "i'd love the opportunity",
    "best practices",
    "drive growth",
    "unlock opportunities",
    "robust strategy",
    "tailored approach",
    "comprehensive solution",
    "data-driven insights",
    "synergy",
    "here's where i'd start",
    "this is where i'd start",
    "the first thing i'd look at",
)

QUARANTINED_CLAIM_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\$?100\s*m(?:illion)?\+?", "The $100M+ aggregate has no audited methodology"),
    (r"\$?53\s*m(?:illion)?\+?", "The $53M+ aggregate has no audited methodology"),
    (r"81%\s+of\s+clients", "The 81% client-improvement claim has no audited denominator or period"),
)


@dataclass
class PricingContext:
    """Upwork acquisition pricing, distinct from JRR's founder advisory rate.

    The $63 profile rate and $50 conditional floor reflect the owner's approved
    Upwork-only lead-acquisition positioning. JRR's current founder advisory
    benchmark is recorded separately so a cheap Upwork profile never becomes the
    default delivery price or a fabricated project quote.
    """

    profile_hourly_rate: float = 63.0
    minimum_hourly_rate: float = 50.0
    minimum_fixed_fee: float | None = None
    founder_advisory_benchmark: float = 175.0
    source_version: str = "upwork-profile-owner-approved-63-usd; jrr-pricing-2026-08-13"

    def __post_init__(self) -> None:
        if self.minimum_hourly_rate < 50 or self.profile_hourly_rate < 50:
            raise ValueError("Hourly pricing values cannot be below the owner-approved $50 floor")
        if self.profile_hourly_rate < self.minimum_hourly_rate:
            raise ValueError("profile_hourly_rate cannot be below minimum_hourly_rate")
        if self.founder_advisory_benchmark < self.minimum_hourly_rate:
            raise ValueError("founder_advisory_benchmark cannot be below minimum_hourly_rate")
        if self.minimum_fixed_fee is not None and self.minimum_fixed_fee <= 0:
            raise ValueError("minimum_fixed_fee must be positive when configured")


@dataclass
class ScoreComponent:
    name: str
    points: int
    reason: str


@dataclass
class JobAnalysis:
    recommendation: str
    score: int
    components: list[ScoreComponent]
    blockers: list[str]
    scope_boundaries: list[str]
    missing_evidence: list[str]
    case_studies: list[dict[str, Any]]
    profile_highlights: list[str]
    pricing: dict[str, Any]
    boost: dict[str, Any]
    proposal_plan: dict[str, Any]

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


def _text(job: Mapping[str, Any]) -> str:
    parts: list[str] = [str(job.get("title") or ""), str(job.get("description") or "")]
    parts.extend(str(item) for item in (job.get("skills") or []))
    return " ".join(parts).lower()


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("$", "")
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*([kmb])?", text, re.I)
    if not match:
        return None
    result = float(match.group(1))
    suffix = (match.group(2) or "").lower()
    result *= {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(suffix, 1)
    return result


def _client(job: Mapping[str, Any]) -> Mapping[str, Any]:
    value = job.get("client")
    return value if isinstance(value, Mapping) else {}


def _first_not_none(*values: Any) -> Any:
    """Return the first observed value without treating a legitimate zero as absent."""

    return next((value for value in values if value is not None), None)


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)


def _contains_bounded_term(text: str, term: str) -> bool:
    """Match a proof-routing term only as a complete token or phrase."""

    normalized = term.strip().casefold()
    if not normalized:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", text.casefold()) is not None


def _match_is_negated(text: str, start: int, end: int) -> bool:
    """Classify the same contrast-bounded clause around one scope phrase."""

    normalized = text.replace("’", "'")
    sentence_boundary = r"[.;!?\n]"
    sentence_prefix = re.split(
        sentence_boundary,
        normalized[max(0, start - 260) : start],
        flags=re.I,
    )[-1]
    sentence_suffix = re.split(
        sentence_boundary,
        normalized[end : end + 220],
        flags=re.I,
    )[0]
    sentence = re.sub(
        r"\s+",
        " ",
        f"{sentence_prefix} <scope> {sentence_suffix}",
    ).strip().casefold()

    allowed_eligibility = (
        r"(?:\b(?:candidates?|applicants?).{0,80}(?:cannot|can't|without).{0,50}"
        r"<scope>.{0,70}\b(?:may|can)\s+(?:still\s+)?apply\b|"
        r"\b(?:will\s+not|won't)\s+reject\b.{0,90}(?:without|cannot|can't).{0,40}"
        r"<scope>|"
        r"\b(?:will|would)\s+(?:accept|consider)\b.{0,90}(?:without|cannot|can't)"
        r".{0,40}<scope>)"
    )
    if re.search(allowed_eligibility, sentence):
        return True

    required_eligibility = (
        r"(?:\b(?:do\s+not|don't)\s+apply\b.{0,100}(?:cannot|can't|without|unless).{0,50}"
        r"<scope>|"
        r"\bunless\b.{0,80}<scope>.{0,80}\b(?:do\s+not|don't)\s+apply\b|"
        r"\b(?:if|who)\b.{0,90}(?:cannot|can't|without).{0,50}<scope>.{0,90}"
        r"(?:do\s+not|don't|must\s+not|should\s+not|cannot|can't)\s+apply\b|"
        r"\b(?:applicants?|candidates?|applications?).{0,100}"
        r"(?:without|cannot|can't|unable).{0,50}<scope>.{0,100}"
        r"(?:(?:must\s+not|should\s+not|cannot|can't|do\s+not|don't)\s+apply|"
        r"(?:will\s+not|won't)\s+be\s+(?:considered|accepted)|"
        r"(?:will|would)\s+be\s+rejected|(?:are|will\s+be)\s+ineligible)\b|"
        r"\bno one\b.{0,80}\bwithout\b.{0,40}<scope>.{0,80}\bshould\s+apply\b|"
        r"\b(?:will\s+not|won't)\s+hire\b.{0,100}\bwithout\b.{0,40}<scope>|"
        r"\b(?:cannot|can't)\s+apply\b.{0,80}\bwithout\b.{0,40}<scope>|"
        r"\bwithout\b.{0,40}<scope>.{0,80}\b(?:cannot|can't)\s+apply\b)"
    )
    if re.search(required_eligibility, sentence):
        return False

    local_boundary = (
        r"[;:!?\n]|,(?!\s*(?:which|that)\b)|"
        r"\b(?:but|however|although|though|yet|and|because|while|whereas|so)\b"
    )
    prefix = re.split(local_boundary, normalized[max(0, start - 180) : start], flags=re.I)[-1]
    suffix = re.split(local_boundary, normalized[end : end + 140], flags=re.I)[0]
    clause = re.sub(r"\s+", " ", f"{prefix} <scope> {suffix}").strip().casefold()

    required_despite_negative = (
        r"(?:\b(?:cannot|can't)\b.{0,90}\bwithout\s+<scope>|"
        r"<scope>.{0,50}\bnot\s+(?:optional|unnecessary|excluded|prohibited)\b|"
        r"<scope>.{0,50}\bnot\s+(?:only|just)\b|"
        r"\b(?:must|should)\s+not\s+(?:omit|skip|avoid|exclude)\b.{0,70}<scope>|"
        r"<scope>.{0,70}\b(?:must|should)\s+not\s+be\s+"
        r"(?:omitted|skipped|avoided|excluded)|"
        r"<scope>.{0,70}\b(?:cannot|can't)\s+be\s+(?:omitted|skipped|avoided|excluded)|"
        r"\b(?:cannot|can't)\s+(?:avoid|omit|skip)\b.{0,60}<scope>|"
        r"\bnot\s+(?:only|just)\b.{0,60}<scope>)"
    )
    if re.search(required_despite_negative, clause):
        return False

    positive_requirement = (
        r"(?:\b(?<!not )(?<!no )(?<!n't )(?:need|needs|require|requires|implement|configure|"
        r"repair|fix|set up)\b.{0,50}<scope>|"
        r"<scope>.{0,50}(?<!not )(?<!n't )\b(?:required|mandatory|essential|broken)|"
        r"<scope>.{0,50}\b(?:must|will)\s+be\s+(?:used|implemented|configured))"
    )
    if re.search(positive_requirement, clause):
        return False

    # After eligibility and double-negative requirements are resolved, any
    # explicit negative marker in the same clause makes the scope an exclusion.
    exclusion_marker = re.compile(
        r"\b(?:no|not|never|without|cannot|can't|isn't|aren't|wasn't|weren't|"
        r"won't|wouldn't|shouldn't|mustn't|don't|doesn't|didn't|unnecessary|"
        r"optional|exclude(?:d|ing)?|omit(?:ted|ting)?|skip(?:ped|ping)?|"
        r"prohibited|forbidden|avoid(?:ed|ing)?|outside|out\s+of\s+scope|"
        r"rather\s+than|instead(?:\s+of)?|except)\b"
    )
    return bool(exclusion_marker.search(clause))


def _has_unnegated_pattern(text: str, pattern: str) -> bool:
    return any(not _match_is_negated(text, match.start(), match.end()) for match in re.finditer(pattern, text, re.I))


def _contains_unnegated_terms(text: str, terms: Iterable[str]) -> bool:
    return any(
        _has_unnegated_pattern(text, rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])")
        for term in terms
    )


def _proof_terms(values: Iterable[str], mapping: Mapping[str, tuple[str, ...]]) -> list[str]:
    terms: list[str] = []
    for value in values:
        normalized = value.strip().lower()
        terms.extend(mapping.get(normalized, (normalized.replace("_", " "),)))
    return list(dict.fromkeys(terms))


def _proof_highlight(record: ProofRecord) -> str | None:
    """Return only highlights whose visible Upwork title is currently verified.

    Portfolio-card titles have drifted independently from the website proof. Until
    those titles are audited in the owner system, the certificate is the only safe
    automatic attachment recommendation.
    """
    return None


def proposal_safe_proof_lines(study: Mapping[str, Any]) -> list[dict[str, str]]:
    """Return canonical, claim-local proof lines that proposal validation can verify exactly."""

    name = str(study.get("name") or "").strip()
    if not name:
        return []
    evidence_periods = {
        _normalise_claim_text(str(evidence.get("text") or "")): str(evidence.get("period") or "").strip()
        for evidence in study.get("claim_evidence") or []
        if isinstance(evidence, Mapping)
    }
    lines: list[dict[str, str]] = []
    for raw_claim in study.get("approved_claims") or []:
        claim = str(raw_claim).strip()
        if not claim:
            continue
        line = f"A relevant example is {name}: {claim}"
        item = {"claim": claim, "line": line}
        period = evidence_periods.get(_normalise_claim_text(claim), "")
        if period:
            item["period"] = period
            item["line_with_period"] = f"{line} Period: {period}"
        lines.append(item)
    return lines


def _has_non_client_service_business_model(job: Mapping[str, Any]) -> bool:
    """Detect explicit marketed models without penalising incidental wording."""

    title = str(job.get("title") or "")
    description = str(job.get("description") or "")
    return bool(
        NON_CLIENT_TITLE_MODEL_PATTERN.search(title)
        or NON_CLIENT_DESCRIPTION_MODEL_PATTERN.search(description)
    )


def select_case_studies(job: Mapping[str, Any], limit: int = 2) -> list[dict[str, Any]]:
    """Return the closest verified proof, with the match strength made explicit."""
    text = _text(job)
    if _has_non_client_service_business_model(job):
        return []

    ranked: list[
        tuple[int, int, ProofRecord, list[str], list[str], list[str]]
    ] = []
    for study in PROOF_MANIFEST:
        routing_tags = [tag for tag in study.allowed_job_tags if tag in TAG_TERMS]
        service_terms = _proof_terms(study.services, SERVICE_TERMS)
        blocked_terms = _proof_terms(study.blocked_job_tags, TAG_TERMS)
        tag_hits = {
            tag: [term for term in TAG_TERMS[tag] if _contains_bounded_term(text, term)]
            for tag in routing_tags
        }
        tag_hits = {tag: terms for tag, terms in tag_hits.items() if terms}
        exact_tag_hits = [tag for tag in tag_hits if tag not in ADJACENT_PROOF_TAGS]
        adjacent_tag_hits = [tag for tag in tag_hits if tag in ADJACENT_PROOF_TAGS]
        vertical_hits = [term for terms in tag_hits.values() for term in terms]
        service_hits = [term for term in service_terms if _contains_bounded_term(text, term)]
        blocked_hits = [term for term in blocked_terms if _contains_bounded_term(text, term)]
        # Service overlap alone is not case-study relevance. Require an audited
        # vertical/business-model tag before exposing any permitted claim.
        if not tag_hits:
            continue
        score = (
            min(72, len(exact_tag_hits) * 24)
            + min(18, len(adjacent_tag_hits) * 6)
            + min(16, len(service_hits) * 4)
        )
        if blocked_hits:
            score -= 24
        if study.status is EvidenceStatus.ROUTE_ONLY_WITH_CAVEAT:
            score -= 3
        if score:
            ranked.append(
                (
                    score,
                    len(exact_tag_hits),
                    study,
                    vertical_hits + service_hits,
                    blocked_hits,
                    exact_tag_hits + adjacent_tag_hits,
                )
            )

    ranked.sort(key=lambda item: (-item[1], -item[0], item[2].name))
    selected: list[dict[str, Any]] = []
    for score, exact_hit_count, study, matched, blocked_hits, matched_tags in ranked:
        if score <= 0 or blocked_hits:
            continue
        selected.append(
            {
                "key": study.key,
                "name": study.name,
                "match_strength": "exact" if exact_hit_count else "adjacent",
                "matched_on": list(dict.fromkeys(matched)),
                "matched_tags": matched_tags,
                "approved_claims": [claim.text for claim in study.permitted_claims],
                "claim_evidence": [
                    {
                        "text": claim.text,
                        "period": claim.period,
                        "source": claim.source,
                        "status": claim.status.value,
                    }
                    for claim in study.permitted_claims
                ],
                "limitations": list(study.limitations),
                "url": study.current_url,
                "evidence_status": study.status.value,
                "source": "Audited dated asset or individual public case-study route",
                "proposal_safe_proof_lines": proposal_safe_proof_lines(
                    {
                        "name": study.name,
                        "approved_claims": [claim.text for claim in study.permitted_claims],
                        "claim_evidence": [
                            {"text": claim.text, "period": claim.period} for claim in study.permitted_claims
                        ],
                    }
                ),
                "highlight": _proof_highlight(study),
            }
        )
        if len(selected) >= limit:
            break
    return selected


def recommended_highlights(job: Mapping[str, Any], case_studies: list[dict[str, Any]] | None = None) -> list[str]:
    studies = case_studies if case_studies is not None else select_case_studies(job, limit=3)
    highlights = [study["highlight"] for study in studies if study.get("highlight")]
    if _contains_any(_text(job), ("google ads", "adwords", "paid search", "ppc", "pmax", "performance max")):
        highlights.append(SEARCH_CERTIFICATION)
    return list(dict.fromkeys(highlights))[:4]


def _proposal_count(job: Mapping[str, Any]) -> int | None:
    value = _first_not_none(job.get("proposal_count"), job.get("proposals"), job.get("applicants_count"))
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if value is None:
        return None
    numbers = [int(number) for number in re.findall(r"\d+", str(value))]
    return max(numbers) if numbers else None


def _recommend_price(job: Mapping[str, Any], context: PricingContext, service_fit: int) -> dict[str, Any]:
    job_type = str(job.get("job_type") or "").lower()
    rate_min = _number(_first_not_none(job.get("hourly_rate_min"), job.get("rate_min")))
    rate_max = _number(_first_not_none(job.get("hourly_rate_max"), job.get("rate_max")))
    budget_min = _number(job.get("budget_min"))
    budget_max = _number(_first_not_none(job.get("budget_max"), job.get("budget")))
    assumptions: list[str] = []

    if "hour" in job_type or rate_min is not None or rate_max is not None:
        if rate_max is None:
            recommended = context.profile_hourly_rate
            position = "profile_rate"
        elif rate_max >= context.profile_hourly_rate:
            recommended = (
                min(rate_min, context.founder_advisory_benchmark)
                if rate_min is not None and rate_min > context.profile_hourly_rate
                else context.profile_hourly_rate
            )
            position = "within_client_range"
        elif rate_max >= context.minimum_hourly_rate and service_fit >= 30:
            recommended = rate_max
            position = "price_conversion_opportunity"
        else:
            recommended = context.minimum_hourly_rate
            position = "above_client_range"
        defensible_low = min(
            max(context.minimum_hourly_rate, rate_min or context.minimum_hourly_rate),
            context.founder_advisory_benchmark,
        )
        defensible_high = max(
            recommended,
            min(rate_max or context.profile_hourly_rate, context.founder_advisory_benchmark),
        )
        if rate_min is not None and rate_max is not None and rate_min > rate_max:
            assumptions.append(
                "The observed client hourly minimum exceeded its maximum, so the defensible range was normalized"
            )
        defensible_range = sorted((defensible_low, defensible_high))
        return {
            "type": "hourly",
            "recommended_bid": recommended,
            "defensible_range": defensible_range,
            "profile_rate": context.profile_hourly_rate,
            "minimum_hourly_rate": context.minimum_hourly_rate,
            "founder_advisory_benchmark": context.founder_advisory_benchmark,
            "pricing_source_version": context.source_version,
            "client_range": [rate_min, rate_max],
            "position": position,
            "requires_owner_approval": True,
            "below_floor_exception": recommended < context.minimum_hourly_rate,
            "live_fee_preview_required": True,
            "expected_net": None,
            "assumptions": assumptions,
        }

    if context.minimum_fixed_fee is None:
        assumptions.append("No current minimum fixed fee is configured, so the owner must choose the fixed bid")
        recommended_fixed: float | None = None
        position = "owner_decision_required"
    elif budget_max is not None and budget_max >= context.minimum_fixed_fee:
        recommended_fixed = budget_max
        position = "match_posted_budget"
    else:
        recommended_fixed = context.minimum_fixed_fee
        position = "price_conversion_opportunity" if service_fit >= 30 else "above_client_budget"

    return {
        "type": "fixed",
        "recommended_bid": recommended_fixed,
        "defensible_range": None,
        "minimum_fixed_fee": context.minimum_fixed_fee,
        "founder_advisory_benchmark": context.founder_advisory_benchmark,
        "pricing_source_version": context.source_version,
        "client_budget": [budget_min, budget_max],
        "position": position,
        "requires_owner_approval": True,
        "below_floor_exception": False,
        "live_fee_preview_required": True,
        "expected_net": None,
        "assumptions": assumptions,
    }


def analyze_job(job: Mapping[str, Any], pricing: PricingContext | None = None) -> JobAnalysis:
    """Evaluate one live job against JRR's current Upwork operating rules."""
    pricing = pricing or PricingContext()
    text = _text(job)
    components: list[ScoreComponent] = []
    blockers: list[str] = []
    boundaries: list[str] = []
    missing: list[str] = []

    google_ads = _contains_any(
        text, ("google ads", "google adwords", "adwords", "paid search", "ppc", "pmax", "performance max", "shopping")
    )
    seo = _contains_any(text, ("seo", "search engine optimization", "organic search", "technical seo"))
    audit = _contains_any(text, ("audit", "review", "account analysis", "second opinion"))
    lead_gen = _contains_any(
        text, ("lead generation", "lead gen", "bookings", "phone calls", "form leads", "qualified leads")
    )
    ecommerce = _contains_any(
        text, ("ecommerce", "e-commerce", "shopify", "woocommerce", "merchant center", "shopping")
    )
    tracking = _contains_unnegated_terms(
        text, ("conversion tracking", "offline conversion", "attribution", "tracking setup", "purchase tracking")
    )
    invited = bool(job.get("invited"))

    service_fit = 0
    if google_ads:
        service_fit += 32
        components.append(ScoreComponent("google_ads_fit", 32, "Google Ads is a core JRR service"))
    if seo:
        service_fit += 20
        components.append(ScoreComponent("seo_fit", 20, "SEO is a core JRR service"))
    if audit:
        service_fit += 10
        components.append(ScoreComponent("audit_fit", 10, "Account audits and reviews are strong entry offers"))
    if lead_gen:
        service_fit += 6
        components.append(ScoreComponent("lead_generation_fit", 6, "Lead-generation outcomes match JRR's proof base"))
    if not (google_ads or seo):
        blockers.append("The role does not contain a core Google Ads or SEO scope")

    for pattern, reason in HARD_SCOPE_PATTERNS:
        if _has_unnegated_pattern(text, pattern):
            blockers.append(reason)

    ecommerce_tracking = _contains_unnegated_terms(
        text,
        (
            "purchase tracking",
            "checkout tracking",
            "ecommerce tracking",
            "ga4 ecommerce",
            "ecommerce pixel",
        ),
    )
    if ecommerce and ecommerce_tracking:
        blockers.append("Ecommerce purchase-tracking implementation is outside JRR's WhatConverts lead-tracking scope")

    unsupported = [
        channel for channel, terms in UNSUPPORTED_CHANNELS.items() if _contains_unnegated_terms(text, terms)
    ]
    if unsupported:
        if google_ads or seo:
            boundaries.append(
                f"Client must accept a Google Ads/SEO-only scope; unsupported channels: {', '.join(unsupported)}"
            )
        else:
            blockers.append(f"The required work is on unsupported channels: {', '.join(unsupported)}")

    employee_style = _contains_unnegated_terms(
        text,
        (
            "full-time",
            "full time",
            "35+ hrs",
            "35+ hours",
            "40 hrs",
            "40 hours",
            "embedded in",
            "direct client ownership",
        ),
    )
    if employee_style:
        blockers.append("The role is employee-style or requires 35+ hours rather than consultancy support")

    agency = "agency" in text
    white_label = _contains_any(text, ("white label", "white-label", "consultancy", "consultant", "fractional"))
    if agency and not white_label and not employee_style:
        boundaries.append(
            "Confirm the agency accepts a white-label consultancy relationship rather than an employee-style role"
        )

    if tracking and not any("Tag Manager" in blocker for blocker in blockers):
        boundaries.append("Confirm the client is open to WhatConverts for lead and offline-conversion attribution")

    client = _client(job)
    payment_verified = bool(
        _first_not_none(client.get("payment_verified"), job.get("client_payment_verified"))
    )
    spent = _number(_first_not_none(client.get("total_spent"), job.get("client_total_spent")))
    hires = _number(_first_not_none(client.get("total_hires"), job.get("client_total_hires")))
    hire_rate = _number(_first_not_none(client.get("hire_rate"), job.get("client_hire_rate")))
    rating = _number(_first_not_none(client.get("rating"), job.get("client_feedback_rating")))
    average_paid = _number(
        _first_not_none(client.get("avg_hourly_rate_paid"), job.get("client_avg_hourly_rate_paid"))
    )

    if payment_verified:
        components.append(ScoreComponent("payment_verified", 7, "Client payment method is verified"))
    else:
        components.append(ScoreComponent("payment_unverified", -5, "Payment verification is missing"))
        missing.append("client payment verification")
    if spent is not None:
        points = 8 if spent >= 10_000 else 4 if spent >= 1_000 else -2
        components.append(ScoreComponent("client_spend", points, f"Client has ${spent:,.0f} of Upwork spend"))
    else:
        missing.append("client total spend")
    if hire_rate is not None:
        points = 5 if hire_rate >= 60 else 0 if hire_rate >= 30 else -5
        components.append(ScoreComponent("hire_rate", points, f"Client hire rate is {hire_rate:g}%"))
    else:
        missing.append("client hire rate")
    if rating is not None and rating >= 4.7:
        components.append(ScoreComponent("client_rating", 4, f"Client rating is {rating:g}"))
    if hires is not None and spent is not None and hires > 0:
        spend_per_hire = spent / hires
        if spend_per_hire < 100:
            components.append(
                ScoreComponent("low_spend_per_hire", -9, f"Client averages only ${spend_per_hire:,.0f} spent per hire")
            )
    if average_paid is not None and average_paid < pricing.minimum_hourly_rate * 0.7:
        components.append(ScoreComponent("low_average_rate", -6, f"Client's average paid rate is ${average_paid:g}/hr"))

    count = _proposal_count(job)
    if invited:
        components.append(ScoreComponent("invited", 10, "The client viewed the profile and invited a response"))
    if count is None:
        missing.append("live proposal count")
    elif count <= 5:
        components.append(ScoreComponent("competition", 12, f"Only {count} proposals"))
    elif count <= 10:
        components.append(ScoreComponent("competition", 8, f"Proposal count is {count}"))
    elif count < 20:
        components.append(ScoreComponent("competition", 3, f"Proposal count is {count}"))
    elif count < 50:
        components.append(ScoreComponent("competition", -10, f"Competition is high at {count} proposals"))
    else:
        components.append(ScoreComponent("competition", -18, f"Competition is very high at {count}+ proposals"))

    studies = select_case_studies(job)
    if studies:
        proof_points = 14 if studies[0]["match_strength"] == "exact" else 7
        components.append(
            ScoreComponent(
                "proof_fit", proof_points, f"Closest proof is {studies[0]['name']} ({studies[0]['match_strength']})"
            )
        )
    else:
        missing.append("a genuinely related individual case study")

    connects = _number(job.get("connects_required"))
    if connects is not None and connects >= 20:
        components.append(ScoreComponent("connect_cost", -3, f"Base application costs {connects:g} Connects"))

    price = _recommend_price(job, pricing, service_fit)
    if price["position"] in {"within_client_range", "profile_rate", "match_posted_budget"}:
        components.append(ScoreComponent("price_fit", 6, "The client range can accommodate the recommended bid"))
    elif price["position"] == "price_conversion_opportunity":
        components.append(ScoreComponent("price_fit", -3, "The posted price needs a consultative conversion"))
    elif price["position"] in {"above_client_range", "above_client_budget"}:
        components.append(ScoreComponent("price_fit", -12, "The client range is below the configured minimum"))

    score = max(0, min(100, sum(component.points for component in components)))
    client_quality = sum(
        component.points
        for component in components
        if component.name
        in {"payment_verified", "client_spend", "hire_rate", "client_rating", "low_spend_per_hire", "low_average_rate"}
    )
    if blockers:
        recommendation = "skip"
    elif price["position"] in {"above_client_range", "above_client_budget"}:
        recommendation = "skip"
    elif price["position"] == "price_conversion_opportunity" and score >= 45:
        recommendation = "price_conversion"
    elif score >= 70 and client_quality >= 8 and not boundaries:
        recommendation = "strong_fit"
    elif score >= 55:
        recommendation = "fit"
    elif score >= 40:
        recommendation = "speculative"
    else:
        recommendation = "skip"

    exact_proof = bool(studies and studies[0]["match_strength"] == "exact")
    should_consider_boost = (
        recommendation == "strong_fit"
        and client_quality >= 12
        and exact_proof
        and price["position"] in {"within_client_range", "profile_rate", "match_posted_budget"}
        and not invited
        and count is not None
        and count < 20
    )
    connect_cap = int(connects) if connects is not None else 8
    max_extra = min(12, connect_cap) if should_consider_boost else 0
    boost = {
        "recommendation": "inspect_live_auction" if should_consider_boost else "no_boost",
        "max_extra_connects": max_extra,
        "reason": (
            "This is an unusually strong fit; inspect the live auction before deciding"
            if should_consider_boost
            else "Invites, ordinary fits, scope boundaries, weak proof, or weak client economics should remain unboosted"
        ),
        "requires_owner_approval": should_consider_boost,
    }

    plan = {
        "opening": "Hey, thanks for the invite." if invited else "Hey, more than happy to take a look at this for you.",
        "proof_order": [study["name"] for study in studies],
        "proof_source": "proposal_safe_proof_lines",
        "external_case_study_links_allowed": False,
        "mention_whatconverts": tracking and not any("Tag Manager" in blocker for blocker in blockers),
        "diagnose_before_access": False,
        "plain_text_only": True,
        "requires_exact_copy_approval": True,
    }

    return JobAnalysis(
        recommendation=recommendation,
        score=score,
        components=components,
        blockers=list(dict.fromkeys(blockers)),
        scope_boundaries=list(dict.fromkeys(boundaries)),
        missing_evidence=list(dict.fromkeys(missing)),
        case_studies=studies,
        profile_highlights=recommended_highlights(job, studies),
        pricing=price,
        boost=boost,
        proposal_plan=plan,
    )


def payload_digest(payload: Mapping[str, Any]) -> str:
    """Return a stable digest used to lock approved copy and commercial terms."""
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def validate_upwork_copy(message: str, *, invited: bool | None = None) -> dict[str, Any]:
    """Validate proposal/message copy without generating or sending anything."""
    errors: list[str] = []
    warnings: list[str] = []
    lowered = message.lower()
    words = re.findall(r"\b[\w'$%+.-]+\b", message)

    if invited is True and not message.startswith("Hey, thanks for the invite."):
        errors.append('Invited proposals must start with "Hey, thanks for the invite."')
    if invited is False and not message.startswith("Hey, more than happy to take a look"):
        warnings.append("The opening does not use the normal non-invited proposal wording")
    if re.search(r"(?m)^\s*(?:[-*#]|\d+[.)]\s)", message):
        errors.append("Upwork proposals must be plain paragraphs without Markdown headings or lists")
    if "—" in message or "–" in message:
        errors.append("Routine Upwork copy cannot contain em dashes or en dashes")
    if re.search(r"\bcalendly\b|mailto:|\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b", lowered):
        errors.append("Pre-contract Upwork copy cannot include booking links or email addresses")
    if _external_urls(message):
        errors.append("Pre-contract Upwork copy cannot include external URLs")
    if re.search(r"(?:\+?\d[\d\s().-]{8,}\d)", message):
        errors.append("Pre-contract Upwork copy appears to include a phone number")
    for phrase in SALESY_PHRASES:
        if phrase in lowered:
            errors.append(f'Remove salesy or template-like wording: "{phrase}"')
    if len(words) > 500:
        errors.append(f"Proposal is {len(words)} words; the maximum is 500")
    elif len(words) > 250:
        warnings.append(f"Proposal is {len(words)} words; shorter often performs better")
    if not re.search(r"\n\s*\n", message):
        warnings.append("Use blank lines between short paragraphs")

    return {
        "valid": not errors,
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys(warnings)),
        "word_count": len(words),
        "copy_sha256": hashlib.sha256(message.encode("utf-8")).hexdigest(),
    }


def validate_proof_claims(message: str, selected_studies: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Allow audited proof only as an exact, claim-local canonical line."""
    errors: list[str] = []
    normalized_message = _normalise_claim_text(_strip_invisible_formatting(message))
    lowered = normalized_message
    selected = {str(study.get("key")): study for study in selected_studies}

    for pattern, reason in QUARANTINED_CLAIM_PATTERNS:
        if re.search(pattern, normalized_message, re.I):
            errors.append(reason)

    if _external_urls(message):
        errors.append("Pre-contract Upwork proof cannot include external URLs; use a verified live profile highlight")

    for record in PROOF_MANIFEST:
        referenced = (
            _normalise_claim_text(record.current_url) in lowered or _normalise_claim_text(record.name) in lowered
        )
        if referenced and record.key not in selected:
            errors.append(f"{record.name} was referenced but was not selected by the proof matcher")

    allowed_lines: list[tuple[Mapping[str, Any], str]] = []
    proof_fragments: list[tuple[Mapping[str, Any], str]] = []
    for study in selected.values():
        lines = proposal_safe_proof_lines(study)
        for item in lines:
            allowed_lines.append((study, _normalise_claim_text(item["line"])))
            if item.get("line_with_period"):
                allowed_lines.append((study, _normalise_claim_text(item["line_with_period"])))
            proof_fragments.append((study, _normalise_claim_text(item["claim"])))
            if item.get("period"):
                proof_fragments.append((study, _normalise_claim_text(item["period"])))

    residual_lines: list[str] = []
    used_studies: set[str] = set()
    for raw_line in message.splitlines():
        line = _normalise_claim_text(raw_line)
        if not line:
            continue
        exact_line: tuple[Mapping[str, Any], str] | None = next(
            ((study, allowed) for study, allowed in allowed_lines if line == allowed),
            None,
        )
        if exact_line:
            used_studies.add(str(exact_line[0].get("key") or ""))
            continue
        residual_lines.append(raw_line)

    residual = "\n".join(residual_lines)
    residual_normalized = _normalise_claim_text(residual)
    any_safe_line_used = bool(used_studies)
    for study, fragment in proof_fragments:
        if fragment and _contains_exact_claim(residual_normalized, fragment):
            errors.append(
                f"Audited proof must use one exact proposal-safe line for the selected case study: {study.get('name')}"
            )
    for study in selected.values():
        name = _normalise_claim_text(str(study.get("name") or ""))
        url = _normalise_claim_text(str(study.get("url") or ""))
        if ((name and name in residual_normalized) or (url and url in residual_normalized)) and str(
            study.get("key") or ""
        ) not in used_studies:
            errors.append(
                f"A selected case study may appear only in its exact proposal-safe proof line: {study.get('name')}"
            )
    if any_safe_line_used and residual_normalized and not _ordinary_nonproof_remainder(residual_normalized):
        errors.append("A generated proof line cannot be combined with extra unstructured copy in the same proof block")
    return {"valid": not errors, "errors": list(dict.fromkeys(errors))}


def _normalise_claim_text(value: str) -> str:
    """Normalize punctuation and spacing without changing a claim's numbers."""

    normalized = unicodedata.normalize("NFKC", _strip_invisible_formatting(value))
    normalized = normalized.casefold().replace("’", "'").replace("–", "-").replace("—", "-")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized.rstrip(".!? ")


def _contains_exact_claim(sentence: str, claim: str) -> bool:
    """Match one full normalized claim as a bounded phrase."""

    pattern = rf"(?<![\w$+.-]){re.escape(claim)}(?![\w%+-])"
    return re.search(pattern, sentence) is not None


def _strip_invisible_formatting(value: str) -> str:
    return "".join(character for character in value if unicodedata.category(character) != "Cf")


_URL_CANDIDATE = re.compile(
    r"(?ix)(?<![@\w])((?:https?://|www\.)[^\s<>()]+|"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z]{2,63}(?:/[^\s<>()]*)?)"
)


def _external_urls(value: str) -> list[str]:
    """Return URL candidates hosted outside Upwork without echoing them in errors."""

    external: list[str] = []
    for raw in _URL_CANDIDATE.findall(value):
        candidate = raw.rstrip(".,;:!?)]}")
        parsed = urlsplit(candidate if "://" in candidate else f"https://{candidate}")
        host = (parsed.hostname or "").casefold()
        if host and host != "upwork.com" and not host.endswith(".upwork.com"):
            external.append(candidate)
    return list(dict.fromkeys(external))


def _ordinary_nonproof_remainder(value: str) -> bool:
    """Recognize only the ordinary proposal contexts allowed beside a proof line."""

    normalized = _normalise_claim_text(value)
    allowed_context = re.compile(
        r"\b(?:i|i'm|i've|i'd|my|you|your|we|account|campaigns?|audit|review|compare|"
        r"scope|project|timeline|available|availability|experience|worked|working|managed|"
        r"rate|bid|budget|fee|price|complete|finish|hours?|weeks?|months?|years?|options?)\b",
        re.I,
    )
    obvious_result = re.compile(
        r"\b(?:roi|roas|revenue|cpl|sales|calls?|forms?|conversions?|"
        r"achieved|generated|made|earned|doubled|tripled|grew|increased|decreased|reduced|"
        r"improved|converted|happened|tracked)\b",
        re.I,
    )
    return bool(allowed_context.search(normalized)) and not obvious_result.search(normalized)


def audit_proposals(proposals: Iterable[Mapping[str, Any]], stale_after_days: int = 14) -> dict[str, Any]:
    """Classify proposal maintenance without recommending wasteful withdrawals."""
    now = datetime.now(UTC)
    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for proposal in proposals:
        status = str(proposal.get("status") or "").lower()
        age_days = _age_in_days(proposal.get("submitted") or proposal.get("submitted_at"), now)
        viewed = bool(proposal.get("client_viewed"))
        interviewed = bool(proposal.get("interview_status") or proposal.get("interviewing"))
        if any(term in status for term in ("archived", "closed", "withdrawn", "declined")):
            action = "no_action_closed"
            reason = "Upwork has already closed or archived this item"
        elif viewed or interviewed:
            action = "keep"
            reason = "The client viewed it or an interview signal exists"
        elif age_days is None or age_days < stale_after_days:
            action = "keep"
            reason = "It is recent enough to remain open"
        else:
            action = "leave_unwithdrawn"
            reason = "Withdrawing does not refund Connects or improve search visibility; leave it unless a specific risk exists"
        counts[action] = counts.get(action, 0) + 1
        rows.append(
            {**dict(proposal), "age_days": age_days, "maintenance_action": action, "maintenance_reason": reason}
        )

    return {
        "summary": counts,
        "proposals": rows,
        "policy": "Decline unsuitable invitations, but do not withdraw old proposals merely for cosmetic cleanup",
    }


def _age_in_days(value: Any, now: datetime) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    relative = re.search(r"(\d+)\s+(hour|day|week|month)s?\s+ago", text, re.I)
    if relative:
        amount = int(relative.group(1))
        multiplier = {"hour": 0, "day": 1, "week": 7, "month": 30}[relative.group(2).lower()]
        return amount * multiplier
    cleaned = re.sub(r"^(?:Initiated|Submitted|Received)\s+", "", text, flags=re.I)
    for fmt in ("%b %d, %Y", "%Y-%m-%d", "%b %d %Y"):
        try:
            parsed = datetime.strptime(cleaned, fmt).replace(tzinfo=UTC)
            return max(0, (now - parsed).days)
        except ValueError:
            continue
    return None
