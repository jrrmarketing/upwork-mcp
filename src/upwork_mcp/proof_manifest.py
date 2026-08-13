"""Audited JRR proof that may be used in Upwork decision support.

This module is deliberately data-only.  It does not rank jobs, draft proposals,
or perform any Upwork action.  Every item in ``permitted_claims`` survived the
July/August 2026 evidence audit.  Conflicting or weak headline claims are kept
out of that collection and documented only as limitations.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class EvidenceStatus(StrEnum):
    """Strength of the source behind a proof record or claim."""

    VERIFIED = "verified"
    VERIFIED_WITH_LIMITATIONS = "verified_with_limitations"
    ROUTE_ONLY_WITH_CAVEAT = "route_only_with_caveat"


@dataclass(frozen=True, slots=True)
class ProofClaim:
    """One exact claim that proposal copy is allowed to repeat."""

    text: str
    period: str
    source: str
    status: EvidenceStatus = EvidenceStatus.VERIFIED


@dataclass(frozen=True, slots=True)
class ProofRecord:
    """Routing metadata and permitted evidence for one case study."""

    key: str
    name: str
    vertical: str
    business_model: str
    services: tuple[str, ...]
    allowed_job_tags: tuple[str, ...]
    blocked_job_tags: tuple[str, ...]
    permitted_claims: tuple[ProofClaim, ...]
    limitations: tuple[str, ...]
    current_url: str
    status: EvidenceStatus

    @property
    def claims(self) -> tuple[ProofClaim, ...]:
        """Compatibility alias that still exposes permitted claims only."""

        return self.permitted_claims


PROOF_MANIFEST: tuple[ProofRecord, ...] = (
    ProofRecord(
        key="cage-and-miles-family-law",
        name="Cage & Miles LLP",
        vertical="legal / family law",
        business_model="multi-location professional services lead generation",
        services=(
            "google_ads",
            "paid_search_restructure",
            "seo",
            "conversion_tracking",
            "whatconverts_attribution",
        ),
        allowed_job_tags=(
            "legal",
            "law_firm",
            "family_law",
            "google_ads",
            "paid_search",
            "multi_location",
            "performance_max_audit",
            "whatconverts",
        ),
        blocked_job_tags=(
            "local_services_ads",
            "seo_content_creation",
            "seo_only",
            "top_ten_keyword_claim",
        ),
        permitted_claims=(
            ProofClaim(
                text="$316.5k in tracked closed case revenue from Google Ads and SEO.",
                period="First three months after the March 2026 engagement start.",
                source=(
                    "jrr-marketing-website/src/routes/"
                    "digital-marketing-case-studies.family-law-firm-marketing-agency.tsx; "
                    "jrr-marketing-website/src/assets/case-studies/"
                    "cage-and-miles-results-lifetime.png"
                ),
            ),
            ProofClaim(
                text="586 tracked calls and forms.",
                period=(
                    "Since March 2026; the route was updated 6 July 2026 but does not "
                    "state the exact measurement cutoff."
                ),
                source=(
                    "jrr-marketing-website/src/routes/"
                    "digital-marketing-case-studies.family-law-firm-marketing-agency.tsx; "
                    "jrr-marketing-website/src/assets/case-studies/"
                    "cage-and-miles-results-lifetime.png"
                ),
            ),
            ProofClaim(
                text="$137 tracked customer acquisition cost per case, including fees and ad spend.",
                period=(
                    "Account lifetime; the route was updated 6 July 2026 but does not "
                    "state the exact measurement cutoff."
                ),
                source=(
                    "jrr-marketing-website/src/routes/"
                    "digital-marketing-case-studies.family-law-firm-marketing-agency.tsx; "
                    "jrr-marketing-website/src/assets/case-studies/"
                    "cage-and-miles-results-lifetime.png"
                ),
            ),
            ProofClaim(
                text="+295% tracked lifetime ROI.",
                period=(
                    "Account lifetime; the route was updated 6 July 2026 but does not "
                    "state the exact measurement cutoff."
                ),
                source=(
                    "jrr-marketing-website/src/routes/"
                    "digital-marketing-case-studies.family-law-firm-marketing-agency.tsx; "
                    "jrr-marketing-website/src/assets/case-studies/"
                    "cage-and-miles-results-lifetime.png"
                ),
            ),
        ),
        limitations=(
            "Revenue and ROI combine Google Ads and SEO where the source says they do.",
            "JRR did not build the existing divorce quiz or all pre-existing site content.",
            "Do not use the route's 93 top-ten keyword figure; the audited supporting asset showed 74.",
            "This case does not support Local Services Ads or SEO-content authorship claims.",
        ),
        current_url=(
            "https://josiahroche.co/digital-marketing-case-studies/"
            "family-law-firm-marketing-agency"
        ),
        status=EvidenceStatus.VERIFIED_WITH_LIMITATIONS,
    ),
    ProofRecord(
        key="melanson-ssdi-law",
        name="Melanson Law Group",
        vertical="legal / social security disability",
        business_model="professional services lead generation",
        services=(
            "google_ads",
            "paid_search_audit",
            "state_and_intent_restructure",
            "audience_targeting",
            "brand_protection",
            "call_tracking",
        ),
        allowed_job_tags=(
            "legal",
            "law_firm",
            "ssdi",
            "disability_law",
            "google_ads",
            "paid_search",
            "account_audit",
            "call_tracking",
        ),
        blocked_job_tags=(
            "closed_revenue",
            "signed_cases",
            "qualified_leads",
            "seo_content_creation",
            "local_services_ads",
        ),
        permitted_claims=(
            ProofClaim(
                text="Google Ads conversions increased from 10 to 51.",
                period="17 March to 25 May 2026 compared with 26 May to 3 August 2026.",
                source=(
                    "jrr-marketing-website/src/assets/case-studies/"
                    "melanson-results-google-ads.svg"
                ),
            ),
            ProofClaim(
                text="Google Ads cost per conversion fell from $423.10 to $68.87.",
                period="17 March to 25 May 2026 compared with 26 May to 3 August 2026.",
                source=(
                    "jrr-marketing-website/src/assets/case-studies/"
                    "melanson-results-google-ads.svg"
                ),
            ),
            ProofClaim(
                text="Google Ads spend fell from $4,231.02 to $3,512.46.",
                period="17 March to 25 May 2026 compared with 26 May to 3 August 2026.",
                source=(
                    "jrr-marketing-website/src/assets/case-studies/"
                    "melanson-results-google-ads.svg"
                ),
            ),
        ),
        limitations=(
            "The $63,000 figure on the route is open quote value, not closed revenue.",
            "Google Ads conversions must not be relabelled as qualified leads or signed cases.",
            "JRR owned the paid-search and tracking work, not all existing SEO content.",
        ),
        current_url=(
            "https://josiahroche.co/digital-marketing-case-studies/"
            "social-security-disability-marketing"
        ),
        status=EvidenceStatus.VERIFIED_WITH_LIMITATIONS,
    ),
    ProofRecord(
        key="replace-your-cushions-ecommerce",
        name="Replace Your Cushions",
        vertical="ecommerce / niche furniture",
        business_model="direct-to-consumer niche ecommerce",
        services=(
            "google_ads",
            "paid_search",
            "performance_max",
            "shopping_feed_segmentation",
            "revenue_attribution",
        ),
        allowed_job_tags=(
            "ecommerce",
            "furniture",
            "google_ads",
            "paid_search",
            "shopping",
            "performance_max",
            "product_feed",
            "revenue_attribution",
        ),
        blocked_job_tags=(
            "go_to_market_strategy",
            "purchase_tracking_repair",
            "seo_only",
            "lead_generation_only",
        ),
        permitted_claims=(
            ProofClaim(
                text="$201,385 in tracked sales.",
                period=(
                    "Since October 2025; the route was published 13 April 2026 but does "
                    "not state the exact measurement cutoff."
                ),
                source=(
                    "jrr-marketing-website/src/routes/"
                    "digital-marketing-case-studies.furniture-marketing-agency.tsx; "
                    "jrr-marketing-website/public/case-study-images/furniture/"
                    "ryc-dashboard-1.webp"
                ),
            ),
            ProofClaim(
                text="694 Google Ads transactions.",
                period=(
                    "Since October 2025; the route was published 13 April 2026 but does "
                    "not state the exact measurement cutoff."
                ),
                source=(
                    "jrr-marketing-website/src/routes/"
                    "digital-marketing-case-studies.furniture-marketing-agency.tsx; "
                    "jrr-marketing-website/public/case-study-images/furniture/"
                    "ryc-dashboard-2.webp"
                ),
            ),
            ProofClaim(
                text="$52.27 tracked customer acquisition cost.",
                period=(
                    "Since October 2025; the route was published 13 April 2026 but does "
                    "not state the exact measurement cutoff."
                ),
                source=(
                    "jrr-marketing-website/src/routes/"
                    "digital-marketing-case-studies.furniture-marketing-agency.tsx; "
                    "jrr-marketing-website/public/case-study-images/furniture/"
                    "ryc-dashboard-2.webp"
                ),
            ),
            ProofClaim(
                text="1,410% return on total marketing investment.",
                period=(
                    "Since October 2025; the route was published 13 April 2026 but does "
                    "not state the exact measurement cutoff."
                ),
                source=(
                    "jrr-marketing-website/src/routes/"
                    "digital-marketing-case-studies.furniture-marketing-agency.tsx; "
                    "jrr-marketing-website/public/case-study-images/furniture/"
                    "ryc-dashboard-3.webp"
                ),
            ),
        ),
        limitations=(
            "Sales are tracked sales attributed by the source, not all company revenue.",
            "The total starts in October 2025 and must not be presented as a 90-day total.",
            "This case does not prove a full go-to-market strategy, standalone tracking repair, or SEO-only work.",
        ),
        current_url=(
            "https://josiahroche.co/digital-marketing-case-studies/"
            "furniture-marketing-agency"
        ),
        status=EvidenceStatus.VERIFIED_WITH_LIMITATIONS,
    ),
    ProofRecord(
        key="dark-shade-window-tinting",
        name="Dark Shade Window Tinting",
        vertical="home services / architectural window tinting",
        business_model="local service lead generation",
        services=(
            "google_ads",
            "website_cro",
            "seo",
            "conversion_tracking",
            "revenue_attribution",
        ),
        allowed_job_tags=(
            "home_services",
            "window_tinting",
            "google_ads",
            "paid_search",
            "local_lead_generation",
            "website_cro",
            "revenue_attribution",
        ),
        blocked_job_tags=(
            "automotive",
            "ecommerce",
            "marketplace",
            "seo_only",
        ),
        permitted_claims=(
            ProofClaim(
                text="10.63x Google Ads ROAS.",
                period=(
                    "Most recent 30-day window in May 2026, five months into the engagement; "
                    "the source does not publish exact start and end dates."
                ),
                source=(
                    "jrr-marketing-website/src/assets/case-studies/"
                    "dark-shade-ads-dashboard.png"
                ),
            ),
            ProofClaim(
                text="$22.6k in ad-attributed revenue.",
                period=(
                    "Most recent 30-day window in May 2026, five months into the engagement; "
                    "the source does not publish exact start and end dates."
                ),
                source=(
                    "jrr-marketing-website/src/assets/case-studies/"
                    "dark-shade-ads-dashboard.png"
                ),
            ),
            ProofClaim(
                text="51 Google Ads conversions.",
                period=(
                    "Most recent 30-day window in May 2026, five months into the engagement; "
                    "the source does not publish exact start and end dates."
                ),
                source=(
                    "jrr-marketing-website/src/assets/case-studies/"
                    "dark-shade-ads-dashboard.png"
                ),
            ),
        ),
        limitations=(
            "The business provides residential and commercial architectural tinting, not automotive tinting.",
            "The permitted metrics are a recent 30-day view and must not be described as lifetime results.",
            "This case is not ecommerce proof.",
        ),
        current_url=(
            "https://josiahroche.co/digital-marketing-case-studies/"
            "window-tinting-marketing-houston"
        ),
        status=EvidenceStatus.VERIFIED,
    ),
    ProofRecord(
        key="japanese-head-spa",
        name="Japanese Head Spa",
        vertical="beauty / wellness",
        business_model="local appointment-based service",
        services=("google_ads", "local_paid_search", "lead_tracking"),
        allowed_job_tags=(
            "beauty",
            "wellness",
            "spa",
            "med_spa_adjacent",
            "google_ads",
            "local_bookings",
            "lead_generation",
        ),
        blocked_job_tags=(
            "clinical_healthcare",
            "ecommerce",
            "seo_only",
            "booking_uplift_claim",
            "cpl_reduction_claim",
        ),
        permitted_claims=(
            ProofClaim(
                text="844.11% actual ROAS.",
                period="Not stated in the audited source asset; never infer or add a period.",
                source=(
                    "jrr-marketing-website/src/assets/case-studies/"
                    "japanese-spa-result-1.png"
                ),
            ),
            ProofClaim(
                text="349 tracked leads.",
                period="Not stated in the audited source asset; never infer or add a period.",
                source=(
                    "jrr-marketing-website/src/assets/case-studies/"
                    "japanese-spa-result-2.png"
                ),
            ),
        ),
        limitations=(
            "The audited assets do not state a measurement period.",
            "Do not use the route's 211% more bookings, 47% lower CPL, or 3.2x conversion-rate claims.",
            "This local wellness proof must not be presented as clinical healthcare or ecommerce proof.",
        ),
        current_url=(
            "https://josiahroche.co/digital-marketing-case-studies/"
            "japanese-spa-marketing"
        ),
        status=EvidenceStatus.VERIFIED_WITH_LIMITATIONS,
    ),
    ProofRecord(
        key="exclusive-tents-b2b",
        name="Exclusive Tents",
        vertical="B2B manufacturing / luxury glamping",
        business_model="high-ticket B2B manufacturer",
        services=(
            "google_ads",
            "landing_pages",
            "geo_targeting",
            "conversion_tracking",
            "lead_attribution",
        ),
        allowed_job_tags=(
            "b2b",
            "manufacturing",
            "industrial",
            "high_ticket",
            "google_ads",
            "landing_pages",
            "geo_targeting",
            "conversion_tracking",
        ),
        blocked_job_tags=(
            "saas",
            "ecommerce",
            "seo_only",
            "revenue_claim",
            "roas_claim",
            "cpl_claim",
        ),
        permitted_claims=(
            ProofClaim(
                text="493 tracked form leads on the main site and 69 on the secondary site.",
                period="Dashboard range from 1 June to 8 February; the source asset does not show the years.",
                source=(
                    "jrr-marketing-website/public/images/portfolio/"
                    "exclusive-tents-results.png"
                ),
            ),
        ),
        limitations=(
            "The source asset does not show the years for its June-to-February range.",
            "Do not use 400% growth, 16x cheaper leads, $79 CPL, or 70+ leads per month without refreshed evidence.",
            "This high-ticket manufacturing proof must not be routed to SaaS or ecommerce jobs.",
        ),
        current_url=(
            "https://josiahroche.co/digital-marketing-case-studies/"
            "b2b-manufacturing-marketing-agency"
        ),
        status=EvidenceStatus.VERIFIED_WITH_LIMITATIONS,
    ),
    ProofRecord(
        key="abodian-cabinet-maker",
        name="Abodian",
        vertical="local business / custom cabinetry",
        business_model="high-ticket local contractor and manufacturer",
        services=(
            "local_seo",
            "google_ads",
            "landing_pages",
            "conversion_tracking",
        ),
        allowed_job_tags=(
            "local_business",
            "trades",
            "home_improvement",
            "cabinet_maker",
            "local_seo",
            "google_ads",
            "landing_pages",
            "lead_generation",
        ),
        blocked_job_tags=(
            "ecommerce",
            "saas",
            "revenue_claim",
            "roas_claim",
        ),
        permitted_claims=(
            ProofClaim(
                text="Average organic traffic increased from 59 to 2,311.",
                period="May 2023 to February 2025.",
                source=(
                    "jrr-marketing-website/src/assets/case-studies/"
                    "cabinet-maker-results-seo.png"
                ),
            ),
            ProofClaim(
                text="Ranking keywords increased from 90 to 2,821.",
                period="May 2023 to February 2025.",
                source=(
                    "jrr-marketing-website/src/assets/case-studies/"
                    "cabinet-maker-results-seo.png"
                ),
            ),
            ProofClaim(
                text="1,478 tracked leads.",
                period="Not stated in the audited lead-dashboard asset; never infer or add a period.",
                source=(
                    "jrr-marketing-website/src/assets/case-studies/"
                    "cabinet-maker-results-leads.png"
                ),
            ),
        ),
        limitations=(
            "The sales value in the audited lead asset is redacted.",
            "Do not use route or index revenue and ROAS figures without refreshed source evidence.",
            "Use the raw SEO endpoints rather than a rounded percentage claim.",
        ),
        current_url=(
            "https://josiahroche.co/digital-marketing-case-studies/"
            "local-business-marketing"
        ),
        status=EvidenceStatus.VERIFIED_WITH_LIMITATIONS,
    ),
    ProofRecord(
        key="clarewell-private-clinic",
        name="Clarewell Clinics",
        vertical="private healthcare / sexual health clinic",
        business_model="appointment-based private clinic",
        services=(
            "google_ads",
            "seo",
            "website_cro",
            "conversion_tracking",
            "team_handover",
        ),
        allowed_job_tags=(
            "healthcare",
            "private_clinic",
            "medical_marketing",
            "google_ads",
            "seo",
            "website_cro",
            "conversion_tracking",
        ),
        blocked_job_tags=(
            "clinical_outcomes",
            "ecommerce",
            "revenue_claim",
            "booking_uplift_claim",
            "rounded_growth_claim",
        ),
        permitted_claims=(
            ProofClaim(
                text="1,164 tracked leads.",
                period="Not stated in the audited lead-dashboard asset; never infer or add a period.",
                source=(
                    "jrr-marketing-website/src/assets/case-studies/"
                    "clarewell-results-1.png"
                ),
            ),
            ProofClaim(
                text="Average organic traffic increased from 18,876 to 65,186.",
                period="May 2023 to September 2023.",
                source=(
                    "jrr-marketing-website/src/assets/case-studies/"
                    "clarewell-results-2.png"
                ),
            ),
        ),
        limitations=(
            "The audited lead asset does not state its measurement period.",
            "Do not use the index's 3,245% organic-growth or 210% booking-uplift figures until reconciled.",
            "This case supports marketing outcomes, not clinical outcomes or revenue claims.",
        ),
        current_url=(
            "https://josiahroche.co/digital-marketing-case-studies/"
            "healthcare-digital-marketing"
        ),
        status=EvidenceStatus.VERIFIED_WITH_LIMITATIONS,
    ),
    ProofRecord(
        key="priority-one-plumbing",
        name="Priority 1 Plumbing",
        vertical="home services / plumbing",
        business_model="local service lead generation",
        services=(
            "google_ads",
            "landing_pages",
            "local_paid_search",
            "conversion_tracking",
        ),
        allowed_job_tags=(
            "plumbing",
            "trades",
            "home_services",
            "google_ads",
            "paid_search",
            "landing_pages",
            "local_lead_generation",
        ),
        blocked_job_tags=(
            "ecommerce",
            "seo_only",
            "revenue_claim",
            "roas_claim",
            "cpl_reduction_claim",
        ),
        permitted_claims=(
            ProofClaim(
                text="1,258 tracked leads.",
                period="September 2023 to July 2024.",
                source=(
                    "jrr-marketing-website/src/assets/case-studies/"
                    "plumber-results-leads.png"
                ),
            ),
            ProofClaim(
                text="33% tracked conversion rate.",
                period="September 2023 to July 2024.",
                source=(
                    "jrr-marketing-website/src/assets/case-studies/"
                    "plumber-results-leads.png"
                ),
            ),
        ),
        limitations=(
            "The sales value in the supporting asset is redacted.",
            "Do not use the conflicting 82% versus 86% CPL reduction, 3x sales, $58 CPL, or 347% ROAS claims.",
            "The asset shows a 33% conversion rate, not the route's 33% to 50% range.",
        ),
        current_url=(
            "https://josiahroche.co/digital-marketing-case-studies/"
            "local-plumber-marketing-agency"
        ),
        status=EvidenceStatus.VERIFIED_WITH_LIMITATIONS,
    ),
    ProofRecord(
        key="drd-criminal-law",
        name="DRD Law LLC",
        vertical="legal / criminal defense",
        business_model="professional services lead generation",
        services=(
            "google_ads",
            "paid_search_restructure",
            "seo",
            "conversion_tracking",
        ),
        allowed_job_tags=(
            "legal",
            "law_firm",
            "criminal_defense",
            "google_ads",
            "paid_search",
            "seo",
            "account_restructure",
        ),
        blocked_job_tags=(
            "local_services_ads",
            "closed_revenue",
            "fully_maintained_attribution",
            "result_screenshot_required",
        ),
        permitted_claims=(
            ProofClaim(
                text="618 Google Ads conversions at $59 per lead.",
                period=(
                    "Twelve-month period stated on the route published 2 April 2026; "
                    "exact start and end dates are not published."
                ),
                source=(
                    "jrr-marketing-website/src/routes/"
                    "digital-marketing-case-studies.criminal-law-firm-marketing-agency.tsx"
                ),
                status=EvidenceStatus.ROUTE_ONLY_WITH_CAVEAT,
            ),
            ProofClaim(
                text="Organic clicks increased from 873 to 6,097, a 598% increase.",
                period=(
                    "Twelve-month period stated on the route published 2 April 2026; "
                    "exact start and end dates are not published."
                ),
                source=(
                    "jrr-marketing-website/src/routes/"
                    "digital-marketing-case-studies.criminal-law-firm-marketing-agency.tsx"
                ),
                status=EvidenceStatus.ROUTE_ONLY_WITH_CAVEAT,
            ),
            ProofClaim(
                text="$197 tracked customer acquisition cost per case.",
                period=(
                    "Twelve-month period stated on the route published 2 April 2026; "
                    "exact start and end dates are not published."
                ),
                source=(
                    "jrr-marketing-website/src/routes/"
                    "digital-marketing-case-studies.criminal-law-firm-marketing-agency.tsx"
                ),
                status=EvidenceStatus.ROUTE_ONLY_WITH_CAVEAT,
            ),
        ),
        limitations=(
            "The public route has no result assets, so this is secondary route-only proof.",
            "The route says WhatConverts lead data was not fully maintained during the period.",
            "Do not strengthen the numbers, infer revenue, or use this case as Local Services Ads proof.",
        ),
        current_url=(
            "https://josiahroche.co/digital-marketing-case-studies/"
            "criminal-law-firm-marketing-agency"
        ),
        status=EvidenceStatus.ROUTE_ONLY_WITH_CAVEAT,
    ),
)


PROOF_BY_KEY: Mapping[str, ProofRecord] = MappingProxyType(
    {record.key: record for record in PROOF_MANIFEST}
)


def get_proof(key: str) -> ProofRecord | None:
    """Return a proof record by canonical key without mutating the manifest."""

    return PROOF_BY_KEY.get(key.strip().lower())
