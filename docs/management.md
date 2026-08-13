# JRR Upwork management policy
This MCP separates live Upwork evidence, deterministic recommendations, owner approval,
and external actions. Read-only discovery may run unattended. Anything that contacts a
client, spends Connects, or changes an Upwork record is prepared and approved separately.

## Opportunity workflow

1. Read both **Best Matches** and **Most Recent**.
2. Hydrate the full job before judging it. Record scope, hours, duration, price, proposals,
   interviews, invitations, Connect cost, client spend, hires, hire rate, average paid rate,
   rating, and payment verification.
3. Classify it as `strong_fit`, `fit`, `price_conversion`, `speculative`, or `skip` with
   separate reasons for service fit, client quality, reachability, pricing, and proof.
4. Inspect the live proposal form. Bind its current questions, duration options, fee/net
   text, base Connect cost, boost auction, and existing-proposal status to the preparation.
5. Draft in Josiah's plain-text consultative voice and answer every screening question.
6. Prepare an expiring one-time action. Show the full copy and terms to Josiah.
7. After a fresh exact approval, arm and commit that unchanged action once. A live-state
   change invalidates the action. Success requires owner-system readback.

`upwork_find_opportunities` and `upwork_screen_job` are read-only on Upwork and write only
minimal decision facts to the private local ledger. They never apply.

## Scope policy

Core work is Google Ads and SEO. Audits and service-business lead generation are strong
entry offers. A low posted price is a warning, not an automatic rejection, when the client
has credible spend, hiring history, account value, or expansion potential.

Hard skips:

- Google Tag Manager or server-side-tagging implementation
- Local Services Ads management
- App/Appsflyer tracking
- Ecommerce purchase or checkout tracking repair
- Meta/social-only work
- Full-time or 35+ hour embedded agency roles

WhatConverts is valid for calls, forms, chats, lead quality, and offline-conversion outcomes.
It is not a substitute for ecommerce purchase tracking. Agency work is viable when it is a
consultancy or white-label relationship rather than an employee-style role.

## Pricing

The current owner-approved Upwork acquisition defaults are:

- Profile rate: **$63/hour**
- Conditional Upwork floor: **$50/hour**
- Current JRR founder-advisory benchmark: **$175/hour**

Use $63 when it fits the client's range. If the client's minimum is materially higher, avoid
an unnecessary low bid and move toward the justified client range, capped at the current
advisory benchmark. Treat $50-$62 as a price-conversion decision. Never go below the floor
or invent a fixed fee. Fixed-price work remains an owner decision until a versioned floor or
scoped estimate exists.

Upwork fees have varied. Never assume a fixed percentage. Read the live fee/net preview and
show it with the exact bid before approval.

## Case-study proof

[`proof_manifest.py`](../src/upwork_mcp/proof_manifest.py) is the proposal-safe source. Each
record has exact permitted claims, measurement period, source, limitations, service ownership,
allowed job tags, blocked job tags, evidence status, and a current public URL.

Evidence order:

1. Dated owner-system asset/export with scope and caveat
2. Individual public case-study route and deep link
3. Website index only for discovery
4. Never use an internal digest or Upwork portfolio-card title as proof

The matcher requires a real vertical or business-model connection; service overlap alone does
not expose a claim. Disputed aggregates and stale card figures are quarantined. In particular,
the MCP refuses `$100M+`, `$53M+`, and `81% of clients` proposal claims until dated methodology
is attached. It does not change the existing Upwork profile title.

Current portfolio-highlight titles must be read from the live owner system. Several old titles
conflict with audited case-study evidence, so the old digest is not an automatic selection list.

## Boosts

Default to no boost. Consider a boost only when all of these are true:

- `strong_fit`
- strong client economics
- exact audited proof match
- fewer than 20 proposals or similarly favourable reachability
- not an invitation
- live auction inspected

The policy caps an initial recommendation at 12 extra Connects and still requires exact owner
approval. It never spends from a recommendation alone. Capture the submitted boost and later
outcome so the report can compare boosted and unboosted performance after enough observations.

## Maintenance

Decline unsuitable invitations with the validated Upwork reason
`Not interested in work described`. Keep future-invitation blocking off unless Josiah gives a
separate instruction. A consultancy note may explain that JRR works with agencies but not as a
full-time embedded team member.

Do not withdraw old proposals merely to make the list look tidy. Withdrawing does not refund
Connects or improve profile search visibility. Keep recent, viewed, or interviewed proposals;
leave old unviewed proposals untouched unless a concrete risk justifies withdrawal.

## Learning loop and privacy

The local ledger is `~/.upwork-mcp/ledger.sqlite3` with owner-only filesystem permissions. It
stores job URLs/titles, policy version, classification, score, bid band, selected proof keys,
boost choice, and verified outcomes. It does not store proposal copy, message bodies, client
names, credentials, or browser DOM.

`upwork_bidding_report` compares view, interview, and hire rates by recommendation, price band,
proof, and boost. Rates remain hidden until the configured minimum submission sample is reached.
The report is descriptive and never rewrites weights automatically.

Qualified interviews and contracts may later be handed to Sales Hub as CRM relationships.
Upwork discovery, bid decisions, proposals, and Connect spend stay in this MCP.
