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
from typing import Any, Literal
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
    "home_services": (
        "home service",
        "plumber",
        "plumbers",
        "plumbing",
        "hvac",
        "roofing",
        "contractor",
        "window tint",
    ),
    "plumbing": ("plumber", "plumbers", "plumbing"),
    "trades": ("trades", "plumber", "plumbers", "plumbing", "hvac", "roofing", "contractor", "cabinet"),
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
PRODUCT_MODEL_PATTERN = re.compile(
    r"\b(?:saas|software|platform|marketplace|(?:mobile|web|online)[- ]+(?:app|application)|"
    r"app|portal|system|suite|dashboard|chatbot|bot|copilot|agent|plugin|extension|api|"
    r"workspace|hub|engine|database|"
    r"(?:mobile|scheduling|booking|workflow|case[- ]management|practice[- ]management|"
    r"field[- ]service(?:\s+management)?|case[- ]intake|client[- ]intake|crm)\s+solution|"
    r"case[- ]manager|virtual[- ]receptionist|(?:ai[- ]+)?assistant|(?:ai[- ]+)?tool|"
    r"crm|automation|cloud[- ]service|online[- ]product|digital[- ]product|"
    r"legal[- ]technology|clio|servicetitan)\b",
    re.I,
)
EXTERNAL_PRODUCT_AUDIENCE_PATTERN = re.compile(
    r"\b(?:law[- ]firms?|lawyers?|attorneys?|family[- ]law(?:\s+firms?|\s+practices?)?|"
    r"criminal[- ]defen[cs]e(?:\s+firms?|\s+lawyers?|\s+attorneys?)?|plumbers?|"
    r"plumbing[- ](?:companies|businesses)|cabinet[- ]makers?|clinics?|dentists?|customers?)\b",
    re.I,
)
EXTERNAL_PRODUCT_COMMERCIALIZATION_PATTERN = re.compile(
    r"(?:"
    r"\b(?:sell|sells|selling|sold|license|licenses|licensed|licensing|offer|offers|offered|"
    r"offering|rent|rents|rented|monetize|monetizes|monetized|charge|charges|charged|"
    r"provide|provides|provided)\b[^.;!?]{0,80}\b(?:it|access|subscriptions?|licenses?|"
    r"saas|software|platform|app|application|portal|system|suite|dashboard|tool|product)\b"
    r"[^.;!?]{0,60}\b(?:to|for|by)\b[^.;!?]{0,45}"
    r"\b(?:law[- ]firms?|lawyers?|attorneys?|family[- ]law|criminal[- ]defen[cs]e|plumbers?|"
    r"plumbing[- ](?:companies|businesses)|cabinet[- ]makers?|customers?)\b|"
    r"\b(?:law[- ]firms?|lawyers?|attorneys?|family[- ]law|criminal[- ]defen[cs]e|plumbers?|"
    r"plumbing[- ](?:companies|businesses)|cabinet[- ]makers?|customers?)\b[^.;!?]{0,65}"
    r"\b(?:subscribe|subscribes|subscribed|buy|buys|purchase|purchases|pay|pays|rent|rents)\b"
    r"[^.;!?]{0,45}\b(?:it|access|subscriptions?|licenses?|software|platform|app|application|"
    r"portal|system|suite|dashboard|tool|product)\b|"
    r"\b(?:it|access|software|platform|app|application|portal|system|suite|dashboard|tool|"
    r"product)\b[^.;!?]{0,45}\bavailable\s+for\s+purchase\b[^.;!?]{0,45}"
    r"\b(?:by|to|for)\b[^.;!?]{0,35}"
    r"\b(?:law[- ]firms?|lawyers?|attorneys?|family[- ]law|criminal[- ]defen[cs]e|plumbers?|"
    r"plumbing[- ](?:companies|businesses)|cabinet[- ]makers?|customers?)\b|"
    r"\b(?:monetize|monetizes|monetized)\b[^.;!?]{0,45}\baccess\b[^.;!?]{0,45}"
    r"\b(?:for|to)\b[^.;!?]{0,35}"
    r"\b(?:law[- ]firms?|lawyers?|attorneys?|family[- ]law|criminal[- ]defen[cs]e|plumbers?|"
    r"plumbing[- ](?:companies|businesses)|cabinet[- ]makers?|customers?)\b|"
    r"\b(?:it|access|software|platform|app|application|portal|system|suite|dashboard|tool|"
    r"product)\b[^.;!?]{0,55}\b(?:offered|available)\s+(?:commercially|for\s+purchase)\b"
    r"[^.;!?]{0,45}\b(?:to|for|by)\b[^.;!?]{0,35}"
    r"\b(?:law[- ]firms?|lawyers?|attorneys?|family[- ]law|criminal[- ]defen[cs]e|plumbers?|"
    r"plumbing[- ]companies|customers?)\b|"
    r"\b(?:charge|charges|charged)\b[^.;!?]{0,45}"
    r"\b(?:law[- ]firms?|lawyers?|attorneys?|family[- ]law|criminal[- ]defen[cs]e|plumbers?|"
    r"plumbing[- ](?:companies|businesses)|cabinet[- ]makers?|customers?)\b[^.;!?]{0,45}\bto\s+access\b"
    r"[^.;!?]{0,25}\b(?:it|this|software|platform|app|system|tool)\b|"
    r"\b(?:law[- ]firms?|lawyers?|attorneys?|family[- ]law|criminal[- ]defen[cs]e|plumbers?|"
    r"plumbing[- ](?:companies|businesses)|cabinet[- ]makers?|customers?)\b[^.;!?]{0,35}"
    r"\b(?:can|may|could)\s+(?:buy|purchase|rent)\b(?:[^.;!?]{0,25}"
    r"\b(?:it|this|access|software|platform|app|system|tool)\b)?"
    r")",
    re.I,
)
EXTERNAL_COMMERCIAL_RELATION_PATTERN = re.compile(
    r"(?:"
    r"\b(?:sell|sells|selling|sold|offer|offers|offered|offering|license|licenses|licensed|"
    r"licensing|market|markets|marketed|marketing|provide|provides|provided|rent|rents|"
    r"rented|monetize|monetizes|monetized)\b[^.;!?]{0,95}"
    r"\b(?:to|for|by)\b[^.;!?]{0,45}"
    r"\b(?:law[- ]firms?|lawyers?|attorneys?|family[- ]law|criminal[- ]defen[cs]e|plumbers?|"
    r"plumbing[- ](?:companies|businesses)|cabinet[- ]makers?|customers?)\b|"
    r"\b(?:law[- ]firms?|lawyers?|attorneys?|family[- ]law|criminal[- ]defen[cs]e|plumbers?|"
    r"plumbing[- ](?:companies|businesses)|cabinet[- ]makers?|customers?)\b[^.;!?]{0,60}"
    r"\b(?:pay|pays|paid|subscribe|subscribes|subscribed|buy|buys|purchase|purchases|"
    r"rent|rents)\b(?:[^.;!?]{0,35}\b(?:monthly|access|it|this|software|platform|app|"
    r"system|tool|product)\b)?"
    r")",
    re.I,
)
MARKETED_PRODUCT_AUDIENCE_PATTERN = re.compile(
    r"(?:"
    r"\b(?:saas|software|platform|marketplace|mobile[- ]app|app|web\s+application|"
    r"online\s+application|portal|online\s+product|digital\s+product|product|system|suite|"
    r"dashboard|chatbot|bot|copilot|agent|plugin|extension|api|workspace|hub|engine|database|"
    r"case[- ]manager|virtual\s+receptionist|"
    r"(?:ai\s+)?assistant|(?:ai\s+)?tool|crm(?:\s+solution)?|"
    r"(?:case[- ]management|practice[- ]management|case\s+intake|client\s+intake|"
    r"mobile|booking|scheduling|field[- ]service(?:\s+management)?)\s+(?:system|solution)|"
    r"workflow\s+automation|cloud\s+service|legal\s+technology)\b[^.;!?]{0,90}"
    r"\b(?:for|to|helps?|helping|serves?|serving|supports?|connects?|connecting|used\s+by)\b"
    r"[^.;!?]{0,75}\b(?:law[- ]firms?|lawyers?|attorneys?|family[- ]law(?:\s+firms?|\s+practices?)?|"
    r"criminal[- ]defen[cs]e(?:\s+firms?|\s+lawyers?|\s+attorneys?)?|plumbers?|"
    r"plumbing\s+companies|clinics?|dentists?)\b"
    r")",
    re.I,
)
INTERNAL_BUSINESS_TOOL_PATTERN = re.compile(
    r"(?:"
    r"\b(?:internal|internally|in[- ]house|our\s+own\s+firm|our\s+(?:team|staff|attorneys?|"
    r"technicians?|cases?|casework|operations)|their\s+(?:team|staff|firm|operations))\b|"
    r"\b(?:subscribe|subscribes|subscribed|purchase|purchases|purchased|adopt|adopts|adopted|"
    r"migrate|migrates|migrated|migrating|integrate|integrates|integrated|integrating)\b"
    r"[^.;!?]{0,90}\b(?:saas|software|platform|app|application|crm|system|tool)\b|"
    r"\b(?:use|uses|using|work|works|working|run|runs|running|have|has|license|licenses|"
    r"licensed|provide|provides|provided|operate|operates|operated|develop|develops|"
    r"developed|build|builds|built)\b[^.;!?]{0,95}"
    r"\b(?:saas|software|platform|app|application|crm|system|tool|clio|servicetitan)\b"
    r"[^.;!?]{0,80}\b(?:internally|in[- ]house|for\s+(?:our|their)\s+(?:internal\s+)?"
    r"(?:use|team|staff|firm|operations|casework)|to\s+our\s+(?:team|staff|attorneys?|"
    r"technicians?)|through\s+our\s+cases?)\b|"
    r"\b(?:saas|software|platform|app|application|crm|system|tool|clio|servicetitan)\b"
    r"[^.;!?]{0,80}\b(?:powers?|is\s+used|helps?)\b[^.;!?]{0,70}"
    r"\b(?:our\s+(?:internal\s+)?(?:team|staff|firm|attorneys?|technicians?|cases?|casework)|"
    r"internally|in[- ]house)\b"
    r")",
    re.I,
)
SERVICE_INTERNAL_PRODUCT_PATTERN = re.compile(
    r"(?:"
    r"\b(?:our|the)\s+(?:family[- ]law\s+(?:firm|team)|criminal[- ]defen[cs]e\s+"
    r"(?:firm|team|attorneys?)|plumbing\s+(?:company|team)|attorneys?|plumbers?|team)\b"
    r"[^.;!?]{0,55}\b(?:has|have|uses?|using|adopted|works?|working|logs?|logging|needs?)\b"
    r"[^.;!?]{0,65}\b(?:saas|software|platform|app|portal|system|suite|dashboard|crm|clio|"
    r"servicetitan)\b|"
    r"\b(?:saas|software|platform|app|portal|system|suite|dashboard|crm|clio|servicetitan)\b"
    r"[^.;!?]{0,55}\b(?:stores?|tracks?|holds?|records?)\b[^.;!?]{0,35}\b(?:leads?|cases?)\b"
    r"|\b(?:our|the)\s+(?:family[- ]law|criminal[- ]defen[cs]e|plumbing)\s+"
    r"(?:firm|company|team)\b[^.;!?]{0,55}"
    r"\b(?:develops?|developing|builds?|building|commissioned|maintains?|maintaining)\b"
    r"[^.;!?]{0,55}\b(?:software|platform|app|portal|system|suite|dashboard|crm|tool)\b"
    r"[^.;!?]{0,45}\bfor\s+(?:its|our|their)\s+(?:lawyers?|attorneys?|plumbers?|staff|team)\b"
    r"|\b(?:we|our\s+(?:team|staff))\b[^.;!?]{0,35}\buse\b[^.;!?]{0,35}"
    r"\b(?:software|platform|app|portal|system|suite|dashboard|crm|tool)\b"
    r"[^.;!?]{0,45}\bto\s+support\s+our\s+(?:family[- ]law|criminal[- ]defen[cs]e|plumbing)\s+"
    r"(?:firm|company|team)\b"
    r")",
    re.I,
)
MARKETED_SERVICE_AUDIENCE_PATTERN = re.compile(
    r"(?:"
    r"\b(?:(?:ppc|seo|lead[- ]generation|website|web[- ]design|accounting|bookkeeping|"
    r"managed[- ]it|digital[- ]marketing|marketing)\s+(?:agency|service)|"
    r"outsourced\s+bookkeeping|bookkeeping\s+service|consultancy|"
    r"call[- ]answering\s+service|(?:ai[- ]voice\s+)?receptionist\s+service|"
    r"coaching\s+program|newsletter|directory|training\s+course|education\s+program|"
    r"membership\s+community)\b[^.;!?]{0,80}"
    r"\b(?:serves?|serving|supports?|supporting|for|to)\b[^.;!?]{0,50}"
    r"\b(?:law[- ]firms?|lawyers?|attorneys?|family[- ]law|criminal[- ]defen[cs]e|plumbers?|"
    r"plumbing[- ](?:companies|businesses)|cabinet[- ]makers?|clinics?|dentists?)\b|"
    r"\b(?:sell|sells|selling|provide|provides|offering|offer)\b[^.;!?]{0,45}"
    r"\b(?:outsourced\s+bookkeeping|bookkeeping|accounting|managed[- ]it|website\s+services?|"
    r"call[- ]answering\s+services?|(?:ai[- ]voice\s+)?receptionist\s+services?|"
    r"coaching\s+programs?|training\s+courses?|newsletters?|directories|consulting|"
    r"consultancy)\b[^.;!?]{0,55}"
    r"\b(?:to|for)\b[^.;!?]{0,40}"
    r"\b(?:law[- ]firms?|lawyers?|attorneys?|family[- ]law|criminal[- ]defen[cs]e|plumbers?|"
    r"plumbing[- ](?:companies|businesses)|cabinet[- ]makers?|clinics?|dentists?)\b"
    r")",
    re.I,
)
MARKETED_COMMERCE_AUDIENCE_PATTERN = re.compile(
    r"(?:"
    r"\b(?:online[- ]store|online[- ]shop|shopify[- ]store|dtc[- ]brand|direct[- ]to[- ]consumer[- ]brand)\b"
    r"[^.;!?]{0,90}\b(?:for|to|sell|sells|selling|serves?|serving)\b[^.;!?]{0,55}"
    r"\b(?:law[- ]firms?|lawyers?|attorneys?|family[- ]law|criminal[- ]defen[cs]e|plumbers?|"
    r"plumbing[- ](?:companies|businesses)|cabinet[- ]makers?)\b|"
    r"\b(?:sell|sells|selling)\b[^.;!?]{0,45}\b(?:products?|goods?|tools?|uniforms?|"
    r"equipment|supplies)\b[^.;!?]{0,55}\b(?:to|for)\b[^.;!?]{0,35}"
    r"\b(?:law[- ]firms?|lawyers?|attorneys?|family[- ]law|criminal[- ]defen[cs]e|plumbers?|"
    r"plumbing[- ](?:companies|businesses)|cabinet[- ]makers?)\b[^.;!?]{0,35}\bonline\b"
    r")",
    re.I,
)
CONSUMED_VENDOR_OR_RESOURCE_PATTERN = re.compile(
    r"\b(?:hired|hire|managed\s+by|uses?|using|subscribes?\s+to|listed\s+in|"
    r"attends?|attending|joined|joins?|buys?|purchases?)\b[^.;!?]{0,65}"
    r"\b(?:(?:ppc|seo|lead[- ]generation|website|web[- ]design|accounting|bookkeeping|"
    r"managed[- ]it|digital[- ]marketing|marketing)\s+(?:agency|service)|"
    r"outsourced\s+bookkeeping|call[- ]answering\s+service|receptionist\s+service|"
    r"coaching\s+program|newsletter|directory|training\s+course|consultancy)\b",
    re.I,
)
HIRING_APPLICATION_CONTEXT_PATTERN = re.compile(
    r"(?:"
    r"\b(?:submit|send|complete|fill\s+out|use)\b[^.;!?]{0,45}"
    r"\b(?:online|web)\s+application(?:\s+portal)?\b|"
    r"\b(?:online|web)\s+application(?:\s+portal)?\b[^.;!?]{0,70}"
    r"\b(?:candidates?|applying|apply|opening|vacancy|role|job)\b"
    r")",
    re.I,
)


HARD_SCOPE_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        r"\b(?:google tag manager|gtm|server[- ]side tag(?:ging)?)\b",
        "Google Tag Manager implementation is outside JRR scope",
    ),
    (
        r"\b(?:local services? ads?|local services? advertising|local services? campaigns?|"
        r"google local services?|google lsa|google guaranteed campaigns?|lsa campaigns?|"
        r"lsa management|lsa expertise|lsas?)\b",
        "Local Services Ads management is outside JRR scope",
    ),
    (
        r"\b(?:appsflyer|app campaigns?|uac management|universal app campaigns?|"
        r"mobile app campaigns?|mobile app installs?|app install campaigns?|"
        r"app promotion campaigns?|ios app|android app|firebase|adjust|branch)\b",
        "App campaign tracking is outside JRR scope",
    ),
)

UNSUPPORTED_CHANNELS = {
    "meta": (
        "meta ads",
        "meta advertising",
        "facebook ads",
        "fb ads",
        "facebook advertising",
        "instagram ads",
        "instagram advertising",
        "paid social",
        "social media advertising",
    ),
    "linkedin": ("linkedin ads", "linkedin ppc", "linkedin campaign manager"),
    "reddit": ("reddit ads", "reddit advertising"),
    "tiktok": ("tiktok ads", "tiktok advertising"),
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
    # Keep owner-system fields clause-separated so a description negation
    # cannot leak backwards onto a positive title or skill.
    return ". ".join(parts).lower()


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


def _term_is_explicitly_excluded(text: str, term: str) -> bool:
    """Return true only for clear target-local service exclusions."""

    normalized = text.casefold().replace("’", "'")
    pattern = rf"(?<![a-z0-9]){re.escape(term.casefold())}(?![a-z0-9])"
    for match in re.finditer(pattern, normalized):
        left = re.split(r"[.;!?\n]", normalized[max(0, match.start() - 120) : match.start()])[-1]
        right = re.split(r"[.;!?\n]", normalized[match.end() : match.end() + 120])[0]
        clause = re.sub(r"\s+", " ", f"{left} <term> {right}").strip()
        if re.search(
            r"\b(?:no|neither)\b[^.;!?]{0,75}<term>|"
            r"\b(?:exclude|excludes|excluded|excluding|omit|omits|omitted|avoid|avoids|"
            r"do\s+not\s+use|don't\s+use)\b[^.;!?]{0,55}<term>|"
            r"<term>[^.;!?]{0,55}\b(?:is|are)?\s*(?:outside|out\s+of)\s+(?:the\s+)?scope\b|"
            r"<term>[^.;!?]{0,55}\b(?:only\s+)?(?:is|are)?\s*not\s+"
            r"(?:in\s+scope|required|needed|part\s+of\s+the\s+scope|the\s+scope)\b",
            clause,
        ):
            return True
    return False


def _contains_positive_terms(text: str, terms: Iterable[str]) -> bool:
    return any(
        _contains_bounded_term(text, term)
        and not _term_is_explicitly_excluded(text, term)
        for term in terms
    )


def _proof_term_is_explicitly_excluded(text: str, term: str) -> bool:
    """Reject a vertical only when the job explicitly says not to use that proof.

    A negative example must never outrank the actual client vertical merely
    because both words appear in the job.  This is deliberately narrower than
    general sentiment analysis: unfamiliar wording simply receives no special
    treatment, while clear instructions such as "plumbing case studies are
    irrelevant" or "do not use family-law proof" are binding.
    """

    normalized = text.casefold().replace("’", "'")
    term_pattern = rf"(?<![a-z0-9]){re.escape(term.casefold())}(?![a-z0-9])"
    for match in re.finditer(term_pattern, normalized):
        left = re.split(r"[.;!?\n]", normalized[max(0, match.start() - 120) : match.start()])[-1]
        right = re.split(r"[.;!?\n]", normalized[match.end() : match.end() + 120])[0]
        clause = re.sub(r"\s+", " ", f"{left} <proof> {right}").strip()
        if re.search(
            r"\b(?:do\s+not|don't|never|avoid|exclude|omit|skip|ignore)\b"
            r"[^.;!?]{0,55}(?:use|mention|include|cite|reference)?[^.;!?]{0,25}<proof>",
            clause,
        ) or re.search(
            r"<proof>[^.;!?]{0,45}(?:case\s+stud(?:y|ies)|proof|example|vertical)?"
            r"[^.;!?]{0,30}\b(?:is|are|seems?|remain)?\s*"
            r"(?:irrelevant|unrelated|inapplicable|not\s+applicable|not\s+relevant)\b",
            clause,
        ) or re.search(
            r"\b(?:not\s+an?|no|without)\b[^.;!?]{0,30}<proof>"
            r"(?:[^.;!?]{0,25}\b(?:firm|company|clients?|customers?|market|business)\b)?|"
            r"\b(?:nothing\s+to\s+do\s+with|unlike|except|other\s+than|rather\s+than)\b"
            r"[^.;!?]{0,30}<proof>|"
            r"<proof>[^.;!?]{0,45}\b(?:outside\s+our\s+market|not\s+our\s+field|"
            r"former\s+market|old\s+niche|not\s+our\s+current\s+(?:market|niche|vertical))\b",
            clause,
        ) or re.search(
            r"\b(?:no\s+longer\s+(?:serve|work\s+with)|stopped\s+working\s+with|"
            r"previously\s+served)\b[^.;!?]{0,40}<proof>|"
            r"<proof>[^.;!?]{0,40}\b(?:was|is)\b[^.;!?]{0,20}"
            r"\b(?:former|previous|old)\b[^.;!?]{0,20}\b(?:market|niche|vertical)\b",
            clause,
        ):
            return True
    return False


def _classify_scope_match(
    text: str,
    start: int,
    end: int,
) -> Literal["required", "excluded", "ambiguous"]:
    """Classify one scope mention without guessing at unfamiliar language.

    Generic negative words are deliberately insufficient: "GTM is not configured"
    describes a missing implementation, while "GTM is not required" removes it
    from scope. Unknown wording is routed to an explicit manual-review state.
    """

    normalized = text.replace("’", "'")
    sentence_prefix = re.split(r"[.!?\n]", normalized[max(0, start - 300) : start], flags=re.I)[-1]
    sentence_suffix = re.split(r"[.!?\n]", normalized[end : end + 260], flags=re.I)[0]
    sentence = re.sub(
        r"\s+",
        " ",
        f"{sentence_prefix} <scope> {sentence_suffix}",
    ).strip().casefold()
    context = re.sub(
        r"\s+",
        " ",
        f"{normalized[max(0, start - 180) : start]} <scope> {normalized[end : end + 320]}",
    ).strip().casefold()
    for contraction, expanded in (
        ("isn't", "is not"),
        ("aren't", "are not"),
        ("wasn't", "was not"),
        ("weren't", "were not"),
        ("won't", "will not"),
        ("wouldn't", "would not"),
        ("shouldn't", "should not"),
        ("mustn't", "must not"),
        ("don't", "do not"),
        ("doesn't", "does not"),
        ("didn't", "did not"),
        ("can't", "cannot"),
        ("we're", "we are"),
        ("it's", "it is"),
    ):
        sentence = sentence.replace(contraction, expanded)
        context = context.replace(contraction, expanded)

    cross_clause_requirement_patterns = (
        r"<scope>[^.!?]{0,120}[.!?;][^.!?]{0,100}"
        r"\b(?:it|this|implementation|deployment|setup|phase\s+(?:two|2)|after\s+launch)\b"
        r"[^.!?]{0,45}(?:(?:\bis|becomes?|remains?|will\s+be)\b[^.!?]{0,20}"
        r"\b(?:mandatory|required|compulsory|indispensable|essential|a\s+core\s+deliverable)\b|"
        r"\b(?:requires?|needs?|own|owns|configure|configures|implement|implements)\b"
        r"[^.!?]{0,20}\b(?:it|this)?\b)",
        r"<scope>[^.!?]{0,120}[.!?;][^.!?]{0,90}"
        r"\b(?:we|you|the\s+(?:role|project|position|implementation))\b[^.!?]{0,45}"
        r"\b(?:need|needs|require|requires|own|owns|configure|implement|transition|transitions)\b"
        r"[^.!?]{0,35}\b(?:it|this|to\s+it)\b",
        r"<scope>[^.!?]{0,100}[;,:][^.!?]{0,75}"
        r"\b(?:implementation|deployment|setup)\b[^.!?]{0,30}"
        r"\b(?:is\s+)?(?:required|mandatory|compulsory|essential|indispensable)\b",
        r"<scope>[^.!?]{0,120}[.!?;,:]"
        r"(?![^.!?]{0,50}\b(?:may|might|could|possibly|potentially|perhaps)\b)"
        r"[^.!?]{0,90}"
        r"\b(?:with\s+(?:the\s+|an?\s+)?)?"
        r"(?:required|mandatory|compulsory|essential|indispensable)\s+"
        r"(?:implementation|deployment|setup|maintenance|ownership)\b"
        r"(?!\s+of\s+(?!(?:it|this)\b))",
        r"<scope>[^.!?]{0,120}[.!?;,:][^.!?]{0,90}"
        r"\b(?:later|eventually|subsequently|after\s+(?:launch|approval|sign[- ]off))\b"
        r"[^.!?]{0,45}\b(?:you|the\s+(?:freelancer|consultant|contractor))\s+"
        r"(?:(?:will\s+)?be|are|become)\s+responsible\s+for\s+(?:it|this)\b"
        r"(?!\s+(?:only\s+)?(?:if|when|depending\b))",
    )
    if any(re.search(pattern, context) for pattern in cross_clause_requirement_patterns):
        return "required"

    uncertain_future_patterns = (
        r"<scope>[^.!?]{0,120}[.!?;,:][^.!?]{0,90}"
        r"\b(?:later|eventually|subsequently|after\s+(?:launch|approval|sign[- ]off))\b"
        r"[^.!?]{0,45}\b(?:you|the\s+(?:freelancer|consultant|contractor))\s+"
        r"(?:may|might|could)\s+(?:be|become)\s+responsible\s+for\s+(?:it|this)\b",
        r"<scope>[^.!?]{0,120}[.!?;,:][^.!?]{0,100}"
        r"\b(?:later\s+)?(?:ownership|responsibility|scope)\b[^.!?]{0,35}"
        r"\b(?:is|remains)\s+(?:undecided|unclear|open|not\s+(?:yet\s+)?decided)\b",
        r"<scope>[^.!?]{0,140}[.!?;,:][^,;:.!?]{0,90}"
        r"\b(?:may|might|could)\s+(?:be|become|require)\b[^,;:.!?]{0,35}"
        r"\b(?:required|mandatory|compulsory|essential|indispensable|responsible|"
        r"implementation|deployment|setup|maintenance|ownership)\b",
        r"<scope>[^.!?]{0,140}[.!?;,:][^,;:.!?]{0,90}"
        r"\b(?:possibly|potentially|perhaps)\b[^,;:.!?]{0,45}"
        r"\b(?:required|mandatory|compulsory|essential|indispensable|responsible|"
        r"implementation|deployment|setup|maintenance|ownership)\b",
        r"<scope>[^.!?]{0,140}[.!?;,:][^.!?]{0,100}"
        r"\b(?:you|the\s+(?:freelancer|consultant|contractor))\s+"
        r"(?:(?:will\s+)?be|are|become)\s+responsible\s+for\s+(?:it|this)\b"
        r"[^.!?]{0,25}\b(?:only\s+)?(?:if|when|depending\s+on)\b",
    )
    if any(re.search(pattern, context) for pattern in uncertain_future_patterns):
        return "ambiguous"

    decisive_exclusion_patterns = (
        r"\bnon[- ]+\s*<scope>\s+(?:approach|solution|tracking|implementation)\b",
        r"\b(?:ask|require|need)\b[^.;!?]{0,45}\byou\s+not\s+(?:to\s+)?"
        r"(?:configure|install|use|touch|manage|change)\b[^.;!?]{0,20}<scope>",
        r"\b(?:please\s+)?refrain\s+from\s+(?:installing|configuring|using|touching|managing)"
        r"[^.;!?]{0,20}<scope>",
        r"\bwhatconverts\b[^.;!?]{0,60}\bin\s+place\s+of\b[^.;!?]{0,20}<scope>",
        r"\babsolutely\s+no\s+<scope>|<scope>\s+(?:is\s+)?(?:disallowed|banned|prohibited)\b",
        r"<scope>\s+is\s+not\s+in\s+scope\b|"
        r"\b(?:freelancer|consultant|contractor)\b[^.;!?]{0,35}\bwill\s+not\s+"
        r"(?:handle|manage|touch|use|configure)\b[^.;!?]{0,20}<scope>",
        r"<scope>\s+must\s+not\s+be\s+(?:touched|handled|managed|configured|installed|used)\b",
        r"\b(?:bypass|bypasses|bypassing|steer\s+clear\s+of)\b[^.;!?]{0,30}<scope>|"
        r"\b(?:independent|agnostic)\b[^.;!?]{0,15}\b(?:of|to)\b[^.;!?]{0,15}<scope>",
        r"<scope>\s*[- ]\s*(?:free|independent|less)\b|"
        r"\b(?:independent|agnostic)\s+(?:of|to)\s+<scope>|"
        r"\boutside\s+<scope>",
        r"<scope>[^.;!?]{0,35}\bcannot\s+be\s+involved\b|"
        r"\bleaves?\b[^.;!?]{0,25}<scope>[^.;!?]{0,20}\buntouched\b",
        r"\bdo\s+not\s+apply\b[^.;!?]{0,60}\bif\b[^.;!?]{0,45}"
        r"\b(?:plan|intend|expect)\b[^.;!?]{0,25}\b(?:install|configure|use|touch)\b"
        r"[^.;!?]{0,20}<scope>",
    )
    if any(re.search(pattern, sentence) for pattern in decisive_exclusion_patterns):
        return "excluded"

    # Resolve grammar that unambiguously binds the target before consulting the
    # broader patterns below.  This prevents an unrelated word such as
    # "optional" from changing "GTM setup is needed", and prevents a generic
    # "require" in "require WhatConverts, not GTM" from being attributed to GTM.
    direct_required_patterns = (
        r"<scope>\s+(?:(?:setup|work|implementation|knowledge|expertise|proficiency|ownership|"
        r"maintenance|deployment|support)\s+)?"
        r"(?:(?:is|remains)\s+)?(?:needed|required|mandatory|necessary|essential|compulsory|"
        r"non[- ]negotiable|a\s+requirement|a\s+must[- ]have)\b",
        r"<scope>\s+implementation\s+must\s+be\s+completed\b",
        r"<scope>.{0,45}\b(?:must\s+be|has\s+to\s+be)\s+"
        r"(?:maintained|completed|handled|owned|supported|deployed)\b",
        r"\b(?:we\s+)?expect\b.{0,30}<scope>\s+(?:implementation|setup|work)\b",
        r"\bplease\s+(?:handle|manage|implement|install|configure|maintain)\b.{0,25}<scope>",
        r"\byou(?:'ll|\s+will)\s+(?:own|handle|manage|maintain)\b.{0,25}<scope>",
        r"\b(?:not\s+looking\s+for|will\s+not\s+hire)\b.{0,60}"
        r"\b(?:someone|anyone|applicants?|candidates?)\b.{0,45}"
        r"\b(?:without|lacking)\b.{0,25}<scope>",
        r"\b(?:do\s+not|don't|will\s+not)\b.{0,30}"
        r"\b(?:want|need|hire|consider)\b.{0,45}"
        r"\b(?:someone|anyone|applicants?|candidates?)\b.{0,35}"
        r"\b(?:unfamiliar|inexperienced)\b.{0,20}\b(?:with|in)\b.{0,15}<scope>",
        r"\b(?:will\s+not|won't)\s+hire\b.{0,45}<scope>\s+novice\b",
        r"<scope>\s*[,()]\s*(?:is\s+)?(?:required|mandatory|essential)\b",
        r"\b(?:do\s+not|don't|are\s+not|will\s+not)\b.{0,30}"
        r"\b(?:consider|considering|want|hire)\b.{0,45}"
        r"\b(?:applicants?|candidates?|anyone|someone)\b.{0,35}"
        r"\b(?:new|unfamiliar|inexperienced)\b.{0,20}\b(?:to|with|in)\b.{0,15}<scope>",
        r"\b(?:applicants?|candidates?)\b[^.;!?]{0,35}\bnew\s+to\b[^.;!?]{0,15}"
        r"<scope>[^.;!?]{0,35}\bshould\s+not\s+apply\b",
        r"<scope>\s+novices?\b[^.;!?]{0,30}\bneed\s+not\s+apply\b",
        r"\bonly\s+<scope>\s*[- ]\s*qualified\b[^.;!?]{0,45}"
        r"\b(?:applicants?|candidates?)\b[^.;!?]{0,25}\bwill\s+be\s+considered\b",
        r"\b(?:applicants?|candidates?)\b[^.;!?]{0,30}\bmust\s+possess\b"
        r"[^.;!?]{0,20}<scope>\s+(?:expertise|knowledge|proficiency|experience)\b",
        r"(?:\band\b|\bplus\b|,)\s*<scope>\s*$",
        r"\bgoogle\s+ads\s+acquisition\b[^.;!?]{0,45}\bfor\b[^.;!?]{0,30}<scope>",
        r"<scope>[^.;!?]{0,75}\b(?:optional|not\s+needed)\b[^.;!?]{0,55}"
        r"[,;]\s*(?:(?:it|this)\s+(?:is\s+)?)?(?:required|mandatory)\b",
        r"<scope>[^.;!?]{0,70}\b(?:initially|currently|for\s+now)\b[^.;!?]{0,45}"
        r"\bbut\b[^.;!?]{0,55}\b(?:you(?:'ll|\s+will)\s+)?"
        r"(?:configure|install|implement|add|set\s+up)\s+(?:it|this)\b",
        r"<scope>[^.;!?]{0,110}\botherwise\b[^.;!?]{0,45}"
        r"\b(?:it|this)\b[^.;!?]{0,25}\b(?:is\s+)?(?:required|mandatory)\b",
        r"<scope>[^.!?]{0,115}(?:[;,]|\b(?:yet|because|so|and)\b)[^.!?]{0,70}"
        r"\b(?:need|needs|expect|expects)\b[^.!?]{0,35}"
        r"(?:(?:you\s+to\s+)?(?:implement|install|configure|add|set\s+up)\s+(?:it|this)|"
        r"(?:it|this)\s+(?:implemented|installed|configured|added|set\s+up))\b",
        r"<scope>[^.!?]{0,115}(?:[;,]|\b(?:yet|because|so|and)\b)[^.!?]{0,55}"
        r"\b(?:please\s+)?(?:implement|install|configure|add|set\s+up)\s+(?:it|this)\b",
        r"<scope>[^.!?]{0,100}[;,:]?[^.!?]{0,45}"
        r"\bimplementation\s+(?:is|will\s+be)\s+(?:required|mandatory|needed)\b",
        r"<scope>[^.!?]{0,80}\b(?:audit|review|reporting)\b[^.!?]{0,45}[;,:]"
        r"[^.!?]{0,65}\b(?:implementation|deployment|setup)\s+(?:phase\s+)?"
        r"(?:requires?|needs?)\s+(?:it|this)\b",
    )
    if any(re.search(pattern, sentence) for pattern in direct_required_patterns):
        return "required"

    direct_excluded_patterns = (
        r"\bwhatconverts\b[^.;!?]{0,65}\b(?:not|instead\s+of|rather\s+than)\b"
        r"[^.;!?]{0,20}<scope>",
        r"\b(?:require|requires|required|need|needs)\b[^.;!?]{0,90}"
        r"\b(?:tracking|work|operate|proceed)\b[^.;!?]{0,30}\bwithout\b"
        r"[^.;!?]{0,20}<scope>",
        r"\b(?:do\s+not|never|will\s+not|should\s+not)\s+"
        r"(?:install|configure|manage|touch|use|fix|change|modify)\b[^.;!?]{0,25}<scope>",
        r"\bno\s+need\s+to\s+(?:fix|install|configure|manage|use|touch)\b"
        r"[^.;!?]{0,25}<scope>",
        r"\b(?:need|needs|require|requires)\b[^.;!?]{0,55}"
        r"(?:to\s+)?(?:avoid|skip|omit|exclude)\b[^.;!?]{0,25}<scope>",
        r"\b(?:need|needs|require|requires)\b[^.;!?]{0,70}"
        r"\b(?:someone|freelancer|consultant|you)\b[^.;!?]{0,35}"
        r"\bnot\s+to\s+(?:touch|use|install|configure|manage|change|modify)\b"
        r"[^.;!?]{0,25}<scope>",
        r"\b(?:need|needs|require|requires)\s+no\s+<scope>\s+work\b",
        r"\b(?:require|requires)\s+(?:no|zero)\s+<scope>\s+changes?\b",
        r"\b(?:part[- ]time|consultant|freelancer|contractor|\d+(?:\.\d+)?\s+hours?)\b"
        r"[^.;!?]{0,55}\bnot\s+(?:a\s+)?<scope>(?:\s+employee\b)?",
        r"\b(?:require|requires|need|needs)\b[^.;!?]{0,65}"
        r"\b(?:zero|no)\s+<scope>\s+(?:involvement|work|changes?)\b",
        r"\b(?:tracking|solution|approach|engagement|project)\b[^.;!?]{0,55}"
        r"\b(?:excludes?|free\s+from|without|non[- ])\s*<scope>",
        r"\bnon[- ]<scope>\s+(?:approach|solution|tracking|implementation)\b|"
        r"\b(?:need|needs|require|requires)\s+no\s+(?:involvement|work|changes?)\s+"
        r"(?:with|from|in)\s+<scope>",
        r"\b(?:ask|require|need)\b[^.;!?]{0,45}\byou\s+not\s+(?:to\s+)?"
        r"(?:configure|install|use|touch|manage|change)\b[^.;!?]{0,20}<scope>",
        r"\b(?:contractor|freelance|fractional|part[- ]time|ten\s+hours?|"
        r"\d+(?:\.\d+)?\s+hours?)\b[^.;!?]{0,55}"
        r"\b(?:rather\s+than|instead\s+of|never|not)\b[^.;!?]{0,25}<scope>",
        r"\bno\s+(?:contact|interaction|involvement)\s+"
        r"(?:with|through|in)\s+<scope>",
        r"<scope>\s+(?:is|remains|stays?)\s+"
        r"(?:out\s+of\s+bounds|off[- ]limits)\b",
    )
    if any(re.search(pattern, sentence) for pattern in direct_excluded_patterns):
        return "excluded"

    # Strong requirement evidence wins over a current-state negative ("not
    # configured") or an earlier excluded use ("not needed for reports, but
    # mandatory for conversions").
    required_patterns = (
        r"<scope>\s+(?:(?:is|remains)\s+)?(?:required|mandatory|essential)\b",
        r"<scope>.{0,55}\bnot\s+(?:optional|unnecessary|expendable)\b",
        r"<scope>.{0,55}\bnot\s+(?:only|just)\s+(?:required|mandatory|essential)\b",
        r"<scope>.{0,55}\b(?:is\s+)?broken\b",
        r"<scope>.{0,55}\b(?:not\s+(?:out\s+of|outside)|non[- ]optional)\b",
        r"<scope>.{0,70}\bnot\s+(?:explicitly\s+)?excluded\b",
        r"<scope>.{0,70}\b(?:never|by\s+no\s+means|hardly|anything\s+but|"
        r"not\s+(?:merely|just)|no\s+longer|cannot\s+be\s+considered)\s+optional\b",
        r"<scope>.{0,70}\bcompulsory\b.{0,45}\b(?:rather\s+than|not)\s+optional\b",
        r"<scope>.{0,70}\b(?:must|has\s+to|will\s+need\s+to)\s+"
        r"(?:be\s+)?(?:used|installed|implemented|configured|set\s+up|fixed|repaired|included)\b",
        r"<scope>.{0,70}\b(?:must\s+(?:not|never)|cannot|can't)\s+be\s+"
        r"(?:omitted|skipped|avoided|excluded)\b",
        r"\b(?:do\s+not|don't|cannot|can't|never)\s+"
        r"(?:omit|skip|avoid|exclude|leave\s+out)\b.{0,65}<scope>",
        r"<scope>.{0,100}\b(?:and|but|although|though|yet)\b.{0,80}"
        r"\b(?:it|this|that)\b.{0,45}\b(?:required|mandatory|essential|must\s+be|"
        r"needs?\s+to\s+be|need\s+you\s+to)\b",
        r"<scope>.{0,100}(?:\b(?:and|but|although|though|yet)\b|[;:])"
        r"(?![^.;!?]{0,55}\bnot\b)[^.;!?]{0,55}"
        r"(?:(?:the\s+)?(?:implementation|installation|setup)\s+)?"
        r"(?:(?:is|remains|will\s+be)\s+)?(?:required|mandatory|essential|needed|necessary)\b",
        r"<scope>.{0,100}\b(?:and|but|although|though|yet)\b.{0,80}"
        r"\b(?:need|needs|must|require|requires)\b.{0,55}\b(?:it|this|that)\b",
        r"\b(?:do\s+not|don't)\s+apply\b.{0,110}\b(?:unless|if|without|cannot|can't)\b"
        r".{0,60}<scope>",
        r"\bif\b.{0,70}\b(?:cannot|can't|without)\b.{0,50}<scope>.{0,80}"
        r"\b(?:do\s+not|don't|must\s+not|should\s+not)\s+apply\b",
        r"\bunless\b.{0,80}<scope>.{0,80}\b(?:do\s+not|don't)\s+apply\b",
        r"\b(?:applicants?|candidates?|applications?|anyone|no\s+one)\b.{0,100}"
        r"\b(?:without|lacking|cannot|can't|unable)\b.{0,55}<scope>.{0,100}"
        r"(?:(?:may|must|should|can)\s+not\s+apply|(?:will\s+not|won't)\s+be\s+"
        r"(?:considered|accepted)|(?:will|would)\s+be\s+rejected|"
        r"(?:are|is|will\s+be)\s+ineligible)\b",
        r"\b(?:will\s+not|won't)\s+hire\b.{0,100}\bwithout\b.{0,45}<scope>",
        r"\bno\s+one\b.{0,70}\bwithout\b.{0,45}<scope>.{0,70}\bshould\s+apply\b",
        r"\b(?:cannot|can't)\s+(?:run|operate|launch|manage)\b.{0,90}\bwithout\b"
        r".{0,45}<scope>",
        r"\b(?:cannot|can't)\s+apply\b.{0,80}\bwithout\b.{0,45}<scope>",
        r"\bwithout\b.{0,45}<scope>.{0,80}\b(?:cannot|can't)\s+apply\b",
        r"\b(?:do\s+not|don't)\s+want\b.{0,55}\b(?:applicants?|candidates?)\b"
        r".{0,70}\b(?:without|lacking|cannot|can't)\b.{0,45}<scope>",
        r"\b(?:do\s+not|don't)\s+(?:want|need)\b.{0,55}"
        r"\b(?:someone|anyone|applicants?|candidates?)\b.{0,70}"
        r"\b(?:without|lacking|cannot|can't)\b.{0,45}<scope>",
        r"\b(?:fix|install|implement|configure|repair|set\s+up|manage|deploy)\b"
        r".{0,65}<scope>",
        r"\b(?:we|you|the\s+(?:client|role|project|account))\b\s+"
        r"(?!(?:do|does)\s+not\b)(?:really\s+)?"
        r"(?:need|needs|require|requires)\b.{0,65}<scope>",
        r"\b(?:but|and|although|however|yet)\b.{0,35}"
        r"\b(?:need|needs|require|requires|fix|install|implement|configure)\b.{0,65}<scope>",
        r"<scope>.{0,40}\b(?:\d{2,3}\+?\s*(?:hrs|hours)|full[- ]time\s+(?:role|position))\b",
    )
    cross_sentence_requirement = re.search(
        r"<scope>[^.!?]{0,120}(?:\b(?:so|and|because|until)\b|[;:.!?])[^.!?]{0,100}"
        r"\b(?:please\s+)?(?:add|install|installed|implement|implemented|configure|configured|"
        r"repair|repaired|fix|fixed|set\s+up)\b"
        r"(?:.{0,35}\b(?:it|this|that)\b)?",
        context,
    )
    if cross_sentence_requirement or any(re.search(pattern, sentence) for pattern in required_patterns):
        return "required"

    if re.search(
        r"\bno\b.{0,25}<scope>.{0,35}\bexperience\b.{0,20}[?;:.!]"
        r".{0,45}\b(?:you\s+)?(?:can|may)\s+(?:still\s+)?apply\b",
        context,
    ):
        return "excluded"

    optional_patterns = (
        r"<scope>[^,;:!?]{0,70}\b(?:not\s+(?:required|needed|necessary|expected|mandatory|essential|"
        r"a\s+(?:requirement|prerequisite))|unnecessary|"
        r"optional|excluded|explicitly\s+excluded|specifically\s+excluded|out\s+of\s+scope|"
        r"outside\s+(?:the\s+)?scope)\b",
        r"<scope>.{0,70}\b(?:(?:should|must|will|would)\s+not|cannot|can't)\s+be\s+used\b",
        r"<scope>.{0,70}\b(?:does\s+not\s+(?:need|have)\s+to|need\s+not)\s+be\s+"
        r"(?:used|installed|implemented|configured|set\s+up)\b",
        r"<scope>.{0,70}\b(?:can|may|could)\s+be\s+(?:left\s+out|omitted|skipped|excluded)\b",
        r"<scope>.{0,70}\b(?:does|do)\s+not\s+(?:form|make\s+up)\s+part\b"
        r".{0,45}\b(?:engagement|scope|role|work)\b",
        r"<scope>.{0,55}\b(?:is|does)\s+not\s+(?:a\s+)?part\b.{0,45}"
        r"\b(?:job|engagement|scope|role|work)\b",
        r"<scope>.{0,55}\b(?:prohibited|forbidden)\b",
        r"<scope>.{0,70}\bnice[- ]to[- ]have\b.{0,55}\bnot\s+a\s+must[- ]have\b",
        r"<scope>.{0,55}\b(?:won't|will\s+not)\s+be\s+necessary\b",
        r"\bno\s+(?:need|requirement|plan|plans|intention|commitment)\b[^,;:!?]{0,75}<scope>",
        r"\b(?:do\s+not|don't|does\s+not|doesn't|will\s+not|won't|are\s+not|aren't|"
        r"is\s+not|isn't)\b[^,;:!?]{0,35}\b(?:need|want|plan|intend|use|using|hire|hiring|"
        r"look(?:ing)?\s+for|expect(?:ing)?)\b[^,;:!?]{0,55}<scope>",
        r"\bno\b.{0,30}<scope>.{0,50}\b(?:commitment|hours?|role|position)\b"
        r".{0,25}\b(?:required|expected)\b",
        r"\bno\b.{0,30}<scope>.{0,50}\b(?:experience\s+)?"
        r"(?:is\s+)?(?:required|needed|necessary|expected|mandatory)\b",
        r"\b(?:is|are|will\s+be|will)\s+not\s+(?:be\s+)?(?:a\s+)?<scope>",
        r"\b(?:avoid|skip|omit|exclude)\b.{0,55}<scope>",
        r"\b(?:do\s+not|don't)\s+(?:touch|change|modify|alter)\b.{0,55}<scope>|"
        r"\b(?:do\s+not|don't)\s+make\b.{0,35}<scope>.{0,30}\bchanges?\b",
        r"\b(?:you\s+are|you're)\s+not\s+responsible\b.{0,45}<scope>|"
        r"<scope>.{0,45}\b(?:is|are)\s+not\s+(?:your|our)\s+responsibility\b",
        r"\b(?:rather\s+than|instead\s+of)\b.{0,50}<scope>|"
        r"<scope>.{0,50}\b(?:rather\s+than|instead\s+of)\b",
        r"\b(?:can|may|could)\s+(?:work|operate|run|proceed)\b.{0,45}\bwithout\b"
        r".{0,35}<scope>",
        r"\b(?:part[- ]time|\d+(?:\.\d+)?\s*hours?)\b.{0,45}\bnot\s+<scope>",
        r"\b(?:fractional|part[- ]time)\b.{0,55}\bnot\s+<scope>",
        r"\beither\b.{0,55}<scope>.{0,70}\bor\b.{0,90}"
        r"\b(?:(?:can|may|could)\s+be\s+used|(?:is|would\s+be)\s+(?:acceptable|fine|allowed)|"
        r"works?\b)",
        r"<scope>.{0,60}\bor\b.{0,80}\b(?:can|may|could)\s+be\s+used\b",
        r"<scope>.{0,65}\bor\b.{0,65}\bwhatconverts\b.{0,45}\b(?:your\s+choice|choice)\b",
        r"\bchoose\b.{0,40}\bbetween\b.{0,45}<scope>.{0,50}\band\b.{0,45}"
        r"\bwhatconverts\b",
        r"\bwhatconverts\b.{0,65}\b(?:acceptable\s+substitute|substitute|replace)\b"
        r".{0,45}<scope>",
        r"\bopen\s+to\b.{0,45}<scope>.{0,45}\bor\b.{0,45}\bwhatconverts\b",
        r"<scope>.{0,70}\bpreferred\b.{0,60}\b(?:not\s+required|optional|acceptable|accepted)\b",
        r"<scope>.{0,70}\bpreferred\b.{0,70}\b(?:part[- ]time|alternative)\b"
        r".{0,50}\b(?:acceptable|accepted|allowed)\b",
        r"<scope>.{0,100}\b(?:but|although|though|yet)\b.{0,80}"
        r"\b(?:whatconverts|an?\s+alternative|another\s+tool)\b.{0,45}"
        r"\b(?:acceptable|accepted|allowed|available)\b.{0,25}\binstead\b",
        r"<scope>.{0,80}\b(?:ideal|preferred)\b.{0,80}\b(?:but|although|though|yet)\b"
        r".{0,70}\b(?:whatconverts|an?\s+alternative|another\s+tool)\b.{0,35}"
        r"\b(?:acceptable|fine|allowed|works?)\b",
        r"<scope>.{0,80}\b(?:ideal|preferred)\b.{0,80}\b(?:but|although|though|yet)\b"
        r".{0,45}\b(?:we\s+are\s+)?open\s+to\b.{0,45}"
        r"\b(?:whatconverts|an?\s+alternative|another\s+tool)\b",
        r"\b(?:applicants?|candidates?|anyone)\b.{0,100}"
        r"\b(?:without|lacking|cannot|can't|unable)\b.{0,55}<scope>.{0,100}"
        r"(?:\b(?:may|can)\s+(?:still\s+)?apply\b|\b(?:are|remain)\s+(?:still\s+)?eligible\b|"
        r"\bwill\s+(?:still\s+)?be\s+(?:considered|accepted)\b)",
        r"\b(?:will\s+not|won't)\s+reject\b.{0,100}\b(?:without|lacking|cannot|can't)\b"
        r".{0,45}<scope>",
        r"\b(?:will|would)\s+(?:accept|consider)\b.{0,100}"
        r"\b(?:without|lacking|cannot|can't)\b.{0,45}<scope>",
        r"\bno\s+one\b.{0,45}\b(?:needs?|requires?)\b.{0,45}<scope>"
        r".{0,45}\bto\s+apply\b",
        r"\b(?:applicants?|candidates?)\b.{0,55}\bneed\s+not\b.{0,55}"
        r"\b(?:know|use|understand|have\s+experience\s+with)\b.{0,40}<scope>",
        r"<scope>.{0,50}\b(?:experience|knowledge)\b.{0,45}"
        r"\b(?:is\s+not\s+a\s+dealbreaker|does\s+not\s+affect\s+eligibility|is\s+a\s+bonus)\b",
        r"\black\s+of\b.{0,40}<scope>.{0,45}\bexperience\b.{0,55}"
        r"\b(?:will\s+not|won't)\s+disqualify\b",
        r"\b(?:applicants?|candidates?)\b.{0,45}\b(?:are\s+)?welcome\b.{0,45}"
        r"\bwithout\b.{0,35}<scope>",
        r"\banyone\b.{0,45}\bcan\s+apply\b.{0,55}\bwith\s+or\s+without\b.{0,35}<scope>",
        r"\baccept\b.{0,35}\bapplicants?\b.{0,55}\bregardless\s+of\b.{0,35}<scope>",
        r"\bno\b.{0,25}<scope>.{0,35}\bexperience\b.{0,20}[?;:.!]"
        r".{0,45}\b(?:can|may)\s+(?:still\s+)?apply\b",
        r"\b(?:freelance|contract\s+basis|part[- ]time(?:\s+only)?)\b.{0,55}"
        r"\b(?:not|no)\b.{0,25}<scope>",
        r"\bno\b.{0,30}<scope>.{0,35}\b(?:requirement|work)\b|"
        r"\bno\s+expectation\b.{0,45}<scope>.{0,35}\bavailability\b",
        r"<scope>.{0,55}\b(?:is|are)\s+not\s+the\s+only\s+option\b.{0,70}"
        r"\bpart[- ]time\b.{0,35}\bworks?\b",
        r"<scope>.{0,45}\b(?:is|are)\s+off\s+the\s+table\b|"
        r"\bwill\s+never\s+become\b.{0,35}<scope>|"
        r"\bcannot\s+offer\b.{0,35}<scope>",
    )
    if any(re.search(pattern, sentence) for pattern in optional_patterns):
        return "excluded"
    return "ambiguous"


def _match_is_negated(text: str, start: int, end: int) -> bool:
    """Compatibility helper for ordinary service-term detection."""

    return _classify_scope_match(text, start, end) == "excluded"


def _pattern_scope_states(
    text: str,
    pattern: str,
) -> set[Literal["required", "excluded", "ambiguous"]]:
    return {
        _classify_scope_match(text, match.start(), match.end())
        for match in re.finditer(pattern, text, re.I)
    }


def _term_scope_states(
    text: str,
    terms: Iterable[str],
) -> set[Literal["required", "excluded", "ambiguous"]]:
    states: set[Literal["required", "excluded", "ambiguous"]] = set()
    for term in terms:
        pattern = rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])"
        states.update(_pattern_scope_states(text, pattern))
    return states


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
    if NON_CLIENT_TITLE_MODEL_PATTERN.search(title):
        return True
    description = str(job.get("description") or "")
    content = f"{title}. {description}"
    description_has_product = PRODUCT_MODEL_PATTERN.search(content) is not None
    for sentence in re.split(r"[.;!?\n]+", content):
        if HIRING_APPLICATION_CONTEXT_PATTERN.search(sentence):
            continue
        if MARKETED_COMMERCE_AUDIENCE_PATTERN.search(sentence):
            return True
        if (
            MARKETED_SERVICE_AUDIENCE_PATTERN.search(sentence)
            and not CONSUMED_VENDOR_OR_RESOURCE_PATTERN.search(sentence)
        ):
            return True
        internal = bool(
            INTERNAL_BUSINESS_TOOL_PATTERN.search(sentence)
            or SERVICE_INTERNAL_PRODUCT_PATTERN.search(sentence)
        )
        commercial = bool(
            EXTERNAL_PRODUCT_COMMERCIALIZATION_PATTERN.search(sentence)
            or (
                description_has_product
                and EXTERNAL_COMMERCIAL_RELATION_PATTERN.search(sentence)
            )
        )
        if commercial and description_has_product:
            return True
        if internal:
            continue
        if MARKETED_PRODUCT_AUDIENCE_PATTERN.search(sentence):
            return True
        if NON_CLIENT_DESCRIPTION_MODEL_PATTERN.search(sentence):
            return True
    return False


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
            tag: [
                term
                for term in TAG_TERMS[tag]
                if _contains_bounded_term(text, term)
                and not _proof_term_is_explicitly_excluded(text, term)
            ]
            for tag in routing_tags
        }
        tag_hits = {tag: terms for tag, terms in tag_hits.items() if terms}
        exact_tag_hits = [tag for tag in tag_hits if tag not in ADJACENT_PROOF_TAGS]
        adjacent_tag_hits = [tag for tag in tag_hits if tag in ADJACENT_PROOF_TAGS]
        vertical_hits = [term for terms in tag_hits.values() for term in terms]
        service_hits = [term for term in service_terms if _contains_bounded_term(text, term)]
        blocked_hits = [
            term
            for term in blocked_terms
            if _contains_bounded_term(text, term)
            and not _term_is_explicitly_excluded(text, term)
        ]
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
    if _contains_positive_terms(
        _text(job),
        ("google ads", "adwords", "paid search", "ppc", "pmax", "performance max"),
    ):
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
        if (
            (rate_min is not None and rate_min < 0)
            or (rate_max is not None and rate_max < 0)
            or (rate_min is not None and rate_max is not None and rate_min > rate_max)
        ):
            assumptions.append(
                "The observed client hourly minimum exceeded its maximum, so owner review is required"
            )
            return {
                "type": "hourly",
                "recommended_bid": context.profile_hourly_rate,
                "defensible_range": [
                    context.minimum_hourly_rate,
                    context.founder_advisory_benchmark,
                ],
                "profile_rate": context.profile_hourly_rate,
                "minimum_hourly_rate": context.minimum_hourly_rate,
                "founder_advisory_benchmark": context.founder_advisory_benchmark,
                "pricing_source_version": context.source_version,
                "client_range": [rate_min, rate_max],
                "position": "invalid_client_range",
                "requires_owner_approval": True,
                "below_floor_exception": False,
                "live_fee_preview_required": True,
                "expected_net": None,
                "assumptions": assumptions,
            }
        if rate_min is not None and rate_max is None:
            assumptions.append(
                "Only the client hourly minimum was observed, so the upper bound must be reviewed"
            )
            recommended = max(context.profile_hourly_rate, rate_min)
            return {
                "type": "hourly",
                "recommended_bid": recommended,
                "defensible_range": sorted(
                    (recommended, max(recommended, context.founder_advisory_benchmark))
                ),
                "profile_rate": context.profile_hourly_rate,
                "minimum_hourly_rate": context.minimum_hourly_rate,
                "founder_advisory_benchmark": context.founder_advisory_benchmark,
                "pricing_source_version": context.source_version,
                "client_range": [rate_min, None],
                "position": "partial_client_range",
                "requires_owner_approval": True,
                "below_floor_exception": False,
                "live_fee_preview_required": True,
                "expected_net": None,
                "assumptions": assumptions,
            }
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

    if (
        (budget_min is not None and budget_min < 0)
        or (budget_max is not None and budget_max < 0)
        or (budget_min is not None and budget_max is not None and budget_min > budget_max)
    ):
        assumptions.append(
            "The observed client fixed-price range is negative or internally inconsistent"
        )
        return {
            "type": "fixed",
            "recommended_bid": None,
            "defensible_range": None,
            "minimum_fixed_fee": context.minimum_fixed_fee,
            "founder_advisory_benchmark": context.founder_advisory_benchmark,
            "pricing_source_version": context.source_version,
            "client_budget": [budget_min, budget_max],
            "position": "invalid_client_budget",
            "requires_owner_approval": True,
            "below_floor_exception": False,
            "live_fee_preview_required": True,
            "expected_net": None,
            "assumptions": assumptions,
        }
    if budget_min is not None and budget_max is None:
        assumptions.append(
            "Only the client fixed-price minimum was observed, so the upper bound must be reviewed"
        )
        recommended_partial = (
            max(budget_min, context.minimum_fixed_fee)
            if context.minimum_fixed_fee is not None
            else None
        )
        return {
            "type": "fixed",
            "recommended_bid": recommended_partial,
            "defensible_range": None,
            "minimum_fixed_fee": context.minimum_fixed_fee,
            "founder_advisory_benchmark": context.founder_advisory_benchmark,
            "pricing_source_version": context.source_version,
            "client_budget": [budget_min, None],
            "position": "partial_client_budget",
            "requires_owner_approval": True,
            "below_floor_exception": False,
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
    requires_scope_review = False

    google_ads = _contains_positive_terms(
        text,
        (
            "google ads",
            "google adwords",
            "adwords",
            "paid search",
            "ppc",
            "pmax",
            "performance max",
            "shopping",
            "google search ads",
            "sem",
            "search advertising",
            "google advertising",
            "google search campaign",
            "google search",
            "search engine marketing",
            "paid media",
        ),
    )
    seo = _contains_positive_terms(text, ("seo", "search engine optimization", "organic search", "technical seo"))
    audit = _contains_positive_terms(text, ("audit", "review", "account analysis", "second opinion"))
    lead_gen = _contains_positive_terms(
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
        states = _pattern_scope_states(text, pattern)
        if "required" in states:
            blockers.append(reason)
        elif "ambiguous" in states:
            boundaries.append(f"Manual scope review required: {reason}")
            requires_scope_review = True

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

    employee_terms = (
        "full-time",
        "full time",
        "35+ hrs",
        "35+ hours",
        "40 hrs",
        "40 hours",
        "embedded in",
        "direct client ownership",
    )
    employee_states = _term_scope_states(text, employee_terms)
    hours_per_week = str(job.get("hours_per_week") or "").casefold()
    structured_high_hours = bool(
        re.search(
            r"\b(?:more\s+than\s+30|30\s*\+|3[1-9]|[4-9]\d)\s*(?:hrs?|hours?)\b",
            hours_per_week,
        )
    )
    described_high_hours = bool(
        re.search(
            r"\b(?:more\s+than\s+30|at\s+least\s+30|30\s*\+|3[1-9]|[4-9]\d)"
            r"\s*(?:hrs?|hours?)"
            r"(?:\s*/\s*(?:week|wk))?\b",
            text,
        )
    )
    contract_to_hire = job.get("contract_to_hire") is True
    employee_commitment = bool(
        re.search(
            r"\b(?:permanent\s+(?:employee\s+role|employment|salaried\s+position|position)|"
            r"salaried\s+position|join\s+our\s+team\s+as\s+an?\s+employee|"
            r"transitions?\s+to\s+permanent\s+(?:employment|employee\s+status)|"
            r"contract[- ]to[- ]hire|temp[- ]to[- ]perm|"
            r"becomes?\s+(?:an?\s+)?staff\s+role|transitions?\s+to\s+(?:it|full[- ]time))\b",
            text,
        )
    )
    employee_style = bool(
        "required" in employee_states
        or structured_high_hours
        or described_high_hours
        or contract_to_hire
        or employee_commitment
    )
    if employee_style:
        blockers.append("The role is employee-style or requires 35+ hours rather than consultancy support")
    elif "ambiguous" in employee_states:
        boundaries.append(
            "Manual scope review required: confirm this is consultancy support rather than an employee-style role"
        )
        requires_scope_review = True

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
    elif price["position"] in {
        "invalid_client_range",
        "partial_client_range",
        "invalid_client_budget",
        "partial_client_budget",
    }:
        components.append(
            ScoreComponent(
                "price_evidence_invalid",
                -12,
                "The observed client hourly range is incomplete or internally inconsistent",
            )
        )
        boundaries.append(
            "Manual scope review required: the observed client hourly range is incomplete or internally inconsistent"
        )
        requires_scope_review = True

    score = max(0, min(100, sum(component.points for component in components)))
    client_quality = sum(
        component.points
        for component in components
        if component.name
        in {"payment_verified", "client_spend", "hire_rate", "client_rating", "low_spend_per_hire", "low_average_rate"}
    )
    if blockers:
        recommendation = "skip"
    elif requires_scope_review:
        recommendation = "scope_review"
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
        "requires_scope_review": requires_scope_review,
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
