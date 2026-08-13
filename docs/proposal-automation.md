# Upwork proposal automation playbook

How to reliably fill and submit an Upwork invite/proposal through the daemon Chrome,
learned the hard way. Pair this with the voice/content rules in
`~/.cursor/rules/upwork-proposals.mdc`, the management policy in `docs/management.md`,
and the audited proof manifest in `src/upwork_mcp/proof_manifest.py`.

## Connect to the logged-in session

The live Upwork session lives in the **background daemon Chrome on CDP port 9222**, not
the Cursor IDE browser. Always connect there:

```python
from patchright.async_api import async_playwright
pw = await async_playwright().start()
browser = await pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
ctx = browser.contexts[0]
page = next((p for p in ctx.pages if 'proposals/' in p.url), ctx.pages[0])
```

If Chrome is running but has no page/tab, open one first via the CDP HTTP endpoint:
`curl -X PUT "http://127.0.0.1:9222/json/new?https://www.upwork.com/nx/find-work/"`.

## Force a desktop viewport (critical)

The daemon Chrome renders at a tiny width, so Upwork serves the **mobile layout**, where
menus/modals are fullscreen overlays that intercept every click. Override device metrics
once per script before doing anything:

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

## The apply form (`/nx/proposals/interview/<uid>/accept`)

- **3 textareas, in order:** `[0]` = Cover Letter, `[1]` = screening Question 1 answer,
  `[2]` = screening Question 2 answer. Fill with `page.locator('textarea').nth(i).fill(...)`.
- **Payment:** two radios, "By milestone" (default) and "By project". Click the text
  "By project" for a one-off audit. Selecting it swaps the milestone description/date
  inputs for a single total amount field.
- **Amount:** `input[placeholder="$0.00"]` → fill the exact approved bid. Read the live
  Upwork fee and net fields; never assume a fixed service-fee percentage.
- **Duration:** a custom `.air3-dropdown-toggle` reading "Select a duration". Open it,
  then click the `li.air3-menu-item` whose exact text is one of: "Less than 1 month",
  "1 to 3 months", "3 to 6 months", "More than 6 months". Open + pick must happen in the
  **same script run** (the menu closes when the script ends). Don't match a bare span,
  the job-details sidebar also prints "Less than 1 month".
- **Submit button:** `Submit proposal`. This is **not** the final send on many jobs.

## Open apply flow (`/nx/proposals/job/~<uid>/apply/`)

Same field map as invite accept, but:
- Often **one textarea** (cover letter only) unless screening questions exist.
- **By milestone is frequently the default.** Click the **By project** radio explicitly. If you
  stay on milestones, Milestone 1 needs a **description** or you get "A description is needed."
- **Amount:** only fill the **enabled** `input[placeholder="$0.00"]`. Disabled fee-breakdown
  inputs (10% service fee, net receive) will timeout if you try to `.fill()` them.
- **Duration:** job sidebar may say "Less than 1 month" while **1 to 3 months** is fine for
  setup + management. Pick what fits the scope.

## Two-step send + fixed-price modal

After a valid **Submit proposal** click, Upwork usually shows **Boost your proposal (optional)**.
The real send is **"Send for X Connects"** (base cost only unless Josiah asked to boost).

Fixed-price jobs then show **"3 things you need to know"**:
1. Check **"Yes, I understand."**
2. Click **Continue**
3. Success → `/nx/proposals/<proposalId>?success` and **"Your proposal was submitted."**

Without the checkbox + Continue, the proposal is **not** sent even if you clicked Send earlier.

The MCP commits only an unexpired prepared proposal. A result is successful only when the
owner system shows a proposal success URL/banner or the stored proposal can be read back.
An unknown result must not be retried automatically.

## Profile highlights (the "Add profile highlights" modal)

Opened by clicking the card titled **"Add a portfolio project"** (also "Add an Upwork job"
/ "Add a certificate"). Modal "Add profile highlights", **max 4 total**.

Read the current titles from this live modal before preparation. Do not rely on the old
case-study digest: several historical card titles conflict with the audited public evidence.
The read-only form inspection opens the chooser, visits every visible tab, reads each card
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
