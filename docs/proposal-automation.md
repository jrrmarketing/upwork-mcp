# Upwork proposal automation playbook

How to reliably fill and submit an Upwork invite/proposal through an already attached owner Chrome
window,
learned the hard way. Pair this with the voice/content rules in
`~/.cursor/rules/upwork-proposals.mdc`, the management policy in `docs/management.md`,
and the audited proof manifest in `src/upwork_mcp/proof_manifest.py`.

## Connect to the existing logged-in window

Browser automation is attach-only. The owner or calling browser integration must already expose a
safe loopback CDP endpoint. The MCP never launches Chrome or creates a browser context:

```python
import os

from patchright.async_api import async_playwright
pw = await async_playwright().start()
browser = await pw.chromium.connect_over_cdp(
    os.environ.get("UPWORK_MCP_CDP_URL", "http://127.0.0.1:9222")
)
ctx = browser.contexts[0]
page = next((p for p in ctx.pages if 'proposals/' in p.url), ctx.pages[0])
```

If no existing browser context is exposed, stop. Do not create a new context or launch a separate
Chrome instance. A new tab may be created only inside an existing attached context.

## Force a desktop viewport (critical)

Override device metrics once per script before doing anything so Upwork exposes the stable desktop
layout:

```python
cdp = await ctx.new_cdp_session(page)
await cdp.send("Emulation.setDeviceMetricsOverride",
               {"width": 1500, "height": 1150, "deviceScaleFactor": 1, "mobile": False})
```

This is the single biggest reliability fix. It must be re-applied in every new script
(each `connect_over_cdp` is a fresh session).

## Click via JS, not native clicks

Upwork's Vue UI layers `air3-fullscreen-container` / `air3-menu-container` overlays that
make Playwright's `.click()` time out with "subtree intercepts pointer events". Drive
clicks with `page.evaluate(() => el.click())` instead. Native `.fill()` on plain
`<textarea>`/`<input>` works fine.

## Invitation accept routes (`/nx/proposals/interview/<uid>/accept`)

The MCP does not click **Accept Interview** while opening a proposal form. That route binds an
invitation identity, not merely a public job ID, and the click can itself change invitation state.
Invitation applications therefore fail closed until the exact invitation ID, linked job identity,
and accept transition can be proven. The ordinary `/nx/proposals/job/~<uid>/apply` route remains
the only automated proposal form.

## Open apply flow (`/nx/proposals/job/~<uid>/apply/`)

Same field map as invite accept, but:
- Often **one textarea** (cover letter only) unless screening questions exist.
- **By milestone is frequently the default.** Click the **By project** radio explicitly. If you
  stay on milestones, Milestone 1 needs a **description** or you get "A description is needed."
- Automated preparation supports fixed `by_project` terms only. It does not create milestone rows,
  and the reversible commercial preflight cannot prove milestone pricing, so `by_milestone`
  proposals must fail closed rather than approving a form the commit cannot reproduce.
- **Amount:** only fill one exact enabled rate or by-project amount control. Fee/net evidence is
  read only from exact Upwork-owned fee and net controls, never from body text or job copy.
- **Duration:** job sidebar may say "Less than 1 month" while **1 to 3 months** is fine for
  setup + management. Pick what fits the scope.

## Two-step send + fixed-price modal

After a valid **Submit proposal** click, Upwork may show **Boost your proposal (optional)**.
Automated commit currently supports **no boost only** because the first Submit click can store the
proposal immediately on some flows. In a confirmed two-step flow, the final control must read
**"Send for X Connects"**, and `X` must exactly equal the approval-bound base Connect cost.

Fixed-price jobs then show **"3 things you need to know"**:
1. Check **"Yes, I understand."**
2. Click **Continue**
3. Success → `/nx/proposals/<proposalId>?success` and **"Your proposal was submitted."**

Without the checkbox + Continue, the proposal is **not** sent even if you clicked Send earlier.

The MCP commits only an unexpired, one-time prepared proposal. It first re-reads the exact
approved application job ID/title/type before exposing form controls. A success URL or banner is
not sufficient: the owner system must expose one exact stored `/nx/proposals/<19-digit-id>` record
whose job identity, normalized cover letter, price, and active/submitted status match the approved
target. `success=false`, an index, a mismatched/unreadable record, or any other unknown result must
not be retried automatically.

After every fill, dropdown, payment, and highlight interaction, the MCP performs one final
non-submit pass over the exact identity, base Connect control, questions, rate/bid, fixed terms,
cover letter, answers, duration, rate-increase state, highlights, fee/net controls, and auction
state. Any silent reset blocks before the first Submit-control query. The result reports the
approved Connect cost, but it does not call that amount spent unless the stored owner-system
proposal readback explicitly exposes and verifies actual Connect usage.

Preparation now treats live form discovery as evidence, not a best-effort scrape. Screening
questions and duration choices each return `complete`, `incomplete`, or `unavailable` plus
diagnostic details. A zero-question form is `complete` only when one cover-letter control, zero
question controls, and the exact textarea count agree. Duration is `complete` only after the
single live menu is opened, all four exact Upwork choices are read, and the menu is dismissed.
Any other state blocks preparation.

Read-only form inspection deliberately leaves fee/net unavailable because a preview captured
before price entry is stale evidence. Preparation uses a reversible commercial preflight: capture
the original rate/by-project amount, enter and read back the exact proposed price, wait for the
preview, read one exact scoped fee control and one exact scoped net control, then restore and read
back the original value and preview. A restoration failure discards the evidence and reports the
unrestored live-form interaction instead of calling the preflight read-only. The exact approval
payload and digest bind `fee_net_price_amount`,
`fee_net_source=scoped_reversible_price_preflight`, normalized
`fee_net_text`/`fee_net_status`, and normalized
`boost_auction_text`/`boost_auction_status`.
Fee/net and exact scoped base-Connect discovery must be complete. Generic “Boost your proposal
with 8 Connects” copy is not current auction state; a top/current/competing bid, rank, slot, bidder
count, or no-bids state is required for a complete auction inspection. The live rate-increase
control is `complete` for hourly work or explicitly `not_applicable` only for fixed-price work.
An absent hourly control is `unavailable`, not proof that rate increases do not apply.

The automated preparation path currently requires `boost_connects=0`. A positive boost remains a
manual exact-approved flow until the live two-stage sequence can prove that the first Submit click
is non-consequential and that the chosen boost is applied before the final send.

Invitation controls such as `Accept Interview` are not part of automated prepare/commit. A suitable
invitation can use this workflow only when an ordinary exact job application form already exists.

## Profile highlights (the "Add profile highlights" modal)

Opened by clicking the card titled **"Add a portfolio project"** (also "Add an Upwork job"
/ "Add a certificate"). Modal "Add profile highlights", **max 4 total**.

Read the current titles from this live modal before preparation. Do not rely on the old
case-study digest: several historical card titles conflict with the audited public evidence.
The read-only form inspection opens the chooser, proves the known `portfolio`, `certifications`,
and Upwork-jobs tabs are all present, visits every visible tab, reads each card
beside a `Select highlight` button without clicking it, and dismisses the chooser. It returns
`available_profile_highlights` with `available_profile_highlights_status`. Preparation is
blocked unless that status is `complete`, and a supplied title must exactly match the live list.

- **Tabs** are `button[role=tab][data-ev-tab=...]` with values `portfolio`,
  `certifications`, and the Upwork-jobs tab. Switch via JS click on the tab button.
- **GOTCHA:** the modal's cards render **outside** `.is-modal-fullscreen`, so a matcher
  scoped to that wrapper finds nothing (empty innerText). Use a **document-wide** matcher:
  find the element with the smallest `innerText` containing the title fragment, walk up to
  the ancestor that contains a `button` with text "Select highlight", then click that
  button.

```python
SEL = r"""(frag)=>{
  let best=null;
  for(const e of document.querySelectorAll('*')){const t=e.innerText||'';
    if(t.includes(frag)){if(!best||t.length<best.t.length)best={el:e,t};}}
  if(!best) return 'notfound:'+frag;
  let card=best.el;
  for(let i=0;i<12&&card;i++){if([...card.querySelectorAll('button')].some(b=>/Select highlight/i.test(b.innerText)))break;card=card.parentElement;}
  const btn=[...card.querySelectorAll('button')].find(b=>/Select highlight/i.test(b.innerText));
  if(!btn) return 'nobtn:'+frag; btn.scrollIntoView({block:'center'}); btn.click(); return 'ok:'+frag;
}"""
```

- Verify selection count by reading `Highlights (N/4)` from `document.body.innerText`.
- Commit with the **"Add to highlights"** button (it's disabled until ≥1 selected).

## Editing after submit (6-hour window)

Upwork allows editing for **up to 6 hours after submit, or until the client opens it**.
Use this to attach highlights you missed.

- Click **"Edit proposal"** → lands on `/nx/proposals/<uid>/edit`.
- The edit form's save button is **"Save"** (not "Submit proposal").
- **GOTCHA:** entering edit mode **clears the duration field** (textareas, payment, and
  amount persist). If you Save without re-setting it you get "Please fix the errors below"
  → "How long do you think this project will take? Value is required." Re-open the duration
  dropdown and pick the option, then Save.
- Success → "Your changes were saved" and it returns to the proposal details page.

## Verify with screenshots

Save `page.screenshot(path=...)` at each step and read it back. Faster and more reliable
than fighting the DOM for confirmation, especially for the highlights order and the
success banners.

## Inside Upwork messages (stay on-platform)

When replying to clients **inside Upwork** (messages, interview chat, post-proposal threads):
- **Do not share Calendly links**, booking URLs, email addresses, phone numbers, or anything
  that pulls them off-platform before a contract exists. Upwork flags it and it can jeopardise
  the account.
- If they want a call, keep it vague and on-platform ("happy to jump on a call once we're set
  up here") or use Upwork's own scheduling flow. No external schedulers in Upwork chat.
- Calendly and direct booking links are fine **outside** Upwork (email, own site, after the
  client has moved off-platform with permission).

Voice and proposal copy rules: `~/.cursor/rules/upwork-proposals.mdc`.
