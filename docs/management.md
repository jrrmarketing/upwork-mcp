# JRR Upwork management policy
This MCP separates live Upwork evidence, deterministic recommendations, owner approval,
and external actions. Read-only discovery may run unattended. Anything that contacts a
client, spends Connects, or changes an Upwork record is prepared and approved separately.

## Opportunity workflow

1. Read both **Best Matches** and **Most Recent**.
2. Hydrate the full job before judging it. Record scope, hours, duration, price, proposals,
   interviews, invitations, Connect cost, client spend, hires, hire rate, average paid rate,
   rating, and payment verification.
3. Classify it as `strong_fit`, `fit`, `price_conversion`, `speculative`, `scope_review`, or `skip` with
   separate reasons for service fit, client quality, reachability, pricing, and proof.
4. Inspect one exact `/jobs/~<job-id>` posting and its matching canonical
   `/nx/proposals/job/~<job-id>/apply` form. Bind the job ID, live title, job type, form URL,
   questions, duration options, exact scoped base Connect cost, boost auction, and
   existing-proposal status to the preparation.
5. Run the reversible commercial preflight with the exact proposed hourly rate or by-project bid;
   bind its scoped fee/net preview only after the original form value is restored exactly.
6. Draft in Josiah's plain-text consultative voice and answer every screening question.
7. Prepare an expiring one-time action. Show the full copy and terms to Josiah.
8. After a fresh exact approval, arm and commit that unchanged action once. A live-state
   change invalidates the action. Success requires owner-system readback.

The commit atomically claims the action before browser work. A claimed action cannot be
replayed, including when Upwork's post-click result is ambiguous; inspect the owner system and
prepare a fresh action instead. The approval tool enforces exact-payload integrity and one-shot
execution, but MCP transport does not cryptographically prove which chat turn came from the
owner. The calling agent must invoke it only after Josiah's fresh later-turn approval under the
canonical communication rule.

Proposal commit navigates directly to the approved application form and reads back the same job
ID, canonical job/form URLs, title, and job type before querying any rate, bid, cover-letter,
screening, duration, highlight, payment, boost, or submit control. Automated fixed-price
preparation supports `by_project` with no milestones. Milestone rows are not created reliably and
the reversible commercial preflight supports by-project terms only, so `by_milestone` automation
fails closed. The live selection and filled values must be readable after entry. Upwork defaults
are never accepted implicitly.

A suitable invitation is not automatically accepted or converted into an application. Automated
prepare/commit is supported only when Upwork already exposes the ordinary exact job application
form; never treat an `Accept Interview` control as part of this workflow.

After all form interactions, commit re-reads every approved live field and state before its first
Submit-control query. Fee/net, auction, and base Connect evidence comes only from exact scoped
Upwork controls, never the whole page or job description. The final no-boost Send label must show
the exact approved base Connect amount. Stored-proposal confirmation proves submission, but the
MCP reports actual Connect spend only when that owner-system readback explicitly verifies it.

A success query or banner is only supporting context. Submission succeeds only when Upwork opens
one exact stored `/nx/proposals/<19-digit-id>` record whose job ID, URL, title, normalized cover
letter, price, and active/submitted status match the approved target. `success=false`, an index,
an unreadable stored identity, or a mismatch remains terminal `unknown` and must not be retried
automatically.

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

Upwork fees have varied. Never assume a fixed percentage. Read the scoped live fee/net preview
only after temporarily entering and reading back the exact price, then restore the original form
value. Bind the preview's exact price and preflight provenance before approval.

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

Proposal proof must use a standalone `proposal_safe_proof_lines` value exposed with the selected
case study. Each generated line binds the study name and one exact permitted claim; the period
variant also binds that claim's audited period. Paraphrases, mixed-client attribution, swapped
periods, and extra proof on the same line fail closed. Ordinary scope, experience, and commercial
copy can remain natural on separate lines. The MCP does not claim to prove arbitrary English has
no implied result; draft and exact owner approval remain required, and audited client evidence may
only enter the auto-submittable copy through the generated line.

Public case-study routes remain evidence metadata, not proposal content. Pre-contract proposals
must not include an external case-study URL; use the exact generated proof line and a verified live
Upwork profile highlight instead.

Current portfolio-highlight titles must be read from the live owner system. Several old titles
conflict with audited case-study evidence, so the old digest is not an automatic selection list.
Proposal preparation requires a `complete` live chooser enumeration and rejects any requested
highlight title that is not in `available_profile_highlights`. An unavailable or incomplete
chooser inspection blocks preparation rather than claiming the title was validated.

The same fail-closed rule applies to screening questions, duration options, scoped base Connect
cost, price-bound fee/net context, and rate-increase applicability. The exact approval payload
binds their live discovery statuses, the commercial-preflight price/source, normalized fee/net
lines, and normalized boost-auction lines. An absent hourly rate-increase control is unavailable;
only a fixed-price form may bind it as not applicable. Empty screening
questions are valid only when the form-control count proves there are genuinely none; an empty or
partial duration menu is never treated as complete.

## Boosts

Default to no boost. Consider a boost only when all of these are true:

- `strong_fit`
- `scope_review` when unsupported-scope wording is not explicit enough to classify safely; this
  state cannot prepare a proposal or recommend a boost until the scope is manually resolved
- strong client economics
- exact audited proof match
- fewer than 20 proposals or similarly favourable reachability
- not an invitation
- live auction inspected

The policy caps an initial recommendation at 12 extra Connects and still requires exact owner
approval. Recommendations and live auction inspection remain available, but automatic proposal
preparation and commit currently require `boost_connects=0`: Upwork may store the proposal on the
first Submit click before a boost dialog can be proven. Generic boost prompts or a Connect amount
alone are not current auction state; require current top/competing bid, rank, slot, bidder count,
or no-bids evidence before describing auction inspection as complete.

At present, automated proposal preparation accepts `boost_connects=0` only. A positive boost must
remain a manual exact-approved flow until the live two-stage Upwork sequence proves that its first
Submit click is non-consequential and the chosen bid is applied before the final send.

### Boost a message

Upwork's **Boost a message** is a separate acquisition product, not a proposal boost or an
ordinary conversation reply. The MCP intentionally does not auto-activate it or expose a
speculative spend tool. It must remain read-only until an authenticated live UI mapping can bind
one exact target, the exact introductory copy, the exact duration, and one owner-approved total
Connect cap, then read those values back from Upwork before any activation. Proposal-boost logic,
generic Send controls, and inferred Connect spend must never be reused for this product.

## Maintenance

Decline unsuitable invitations with the validated Upwork reason
`Not interested in work described`. Keep future-invitation blocking off unless Josiah gives a
separate instruction. A consultancy note may explain that JRR works with agencies but not as a
full-time embedded team member.

Do not withdraw old proposals merely to make the list look tidy. Withdrawing does not refund
Connects or improve profile search visibility. Keep recent, viewed, or interviewed proposals;
leave old unviewed proposals untouched unless a concrete risk justifies withdrawal.

## Learning loop and privacy

The local ledger is `~/.upwork-mcp/ledger.sqlite3` with owner-only filesystem permissions. Its
learning tables store job URLs/titles, policy version, classification, score, bid band, selected
proof keys, boost choice, and verified outcomes. They do not store proposal copy, message bodies,
client names, credentials, or browser DOM.

The same private SQLite file briefly stores the exact proposal, message, or other action payload
while its prepared action is pending. That temporary payload is required to bind a later approval
to unchanged copy and terms. It is redacted in the same atomic update that claims the action, or
consumes it after successful readback, or on the next access after the action expires. Legacy
claimed, consumed, and expired rows are also scrubbed on access. Digests, idempotency keys, action
state, and audit timestamps remain for replay protection.

`upwork_bidding_report` compares view, interview, and hire rates by recommendation, price band,
proof, and boost. Rates remain hidden until the configured minimum submission sample is reached.
The report is descriptive and never rewrites weights automatically.

Qualified interviews and contracts may later be handed to Sales Hub as CRM relationships.
Upwork discovery, bid decisions, proposals, and Connect spend stay in this MCP.
