"""Approval-gated Upwork invitation actions."""

from __future__ import annotations

import asyncio
import re
from typing import Any, Literal

from pydantic import Field, field_validator

from ..browser.client import get_browser
from ..prepared_actions import prepare_action
from ..strategy import validate_upwork_copy
from .proposals import StrictToolModel, approval_gate, validate_job_or_invitation_url


class InvitationsParams(StrictToolModel):
    """Read-only invitation-list parameters."""

    limit: int = Field(default=20, ge=1, le=50)


class DeclineInvitationParams(StrictToolModel):
    """Exact owner-approved invitation decline."""

    invitation_url: str = Field(description="Full Upwork invitation URL")
    reason: Literal["Not interested in work described"] = Field(
        default="Not interested in work described",
        description="Exact validated Upwork decline reason",
    )
    note: str | None = Field(default=None, max_length=5000, description="Optional exact note to the client")
    block_future_invitations: Literal[False] = Field(
        default=False,
        description="Safety lock: this workflow never blocks future invitations",
    )
    approved: bool = False
    approval_sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    action_id: str | None = Field(default=None, min_length=1, max_length=128)

    _validate_invitation_url = field_validator("invitation_url")(validate_job_or_invitation_url)

    @field_validator("note")
    @classmethod
    def _note_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Use null instead of a blank decline note")
        return value


def invitation_decline_payload(params: DeclineInvitationParams) -> dict[str, Any]:
    return {
        "invitation_url": params.invitation_url,
        "reason": params.reason,
        "note": params.note,
        "block_future_invitations": params.block_future_invitations,
    }


async def get_invitations(params: InvitationsParams | None = None) -> list[dict[str, Any]]:
    """Return current invitations without accepting or declining any of them."""
    params = params or InvitationsParams()
    browser = get_browser()
    await browser.ensure_logged_in()
    async with browser.operation() as page:
        await page.goto("https://www.upwork.com/nx/proposals/", wait_until="domcontentloaded")
        links = await page.query_selector_all('a[href*="/proposals/interview/"]')
        invitations: list[dict[str, Any]] = []
        seen: set[str] = set()
        for link in links:
            href = await link.get_attribute("href")
            if not href:
                continue
            url = href if href.startswith("http") else f"https://www.upwork.com{href}"
            if url in seen:
                continue
            seen.add(url)
            title = _normalise((await link.text_content()) or "")
            try:
                summary = _normalise(
                    await link.evaluate(
                        "element => (element.closest('article, section, tr, li, [data-test*=proposal]') || element.parentElement)?.innerText || ''"
                    )
                )
            except Exception:
                summary = title
            invitations.append(
                {
                    "title": title,
                    "invitation_url": url,
                    "summary": summary[:2_000],
                }
            )
            if len(invitations) >= params.limit:
                break
    return invitations


def prepare_invitation_decline(params: DeclineInvitationParams) -> dict[str, Any]:
    """Prepare exact decline copy locally without opening Upwork."""
    payload = invitation_decline_payload(params)
    validation = validate_upwork_copy(params.note or "No client note") if params.note else {
        "valid": True,
        "errors": [],
        "warnings": [],
    }
    prepared = prepare_action("invitation_decline", payload) if validation["valid"] else None
    return {
        "ready_for_owner_approval": validation["valid"],
        "errors": validation["errors"],
        "warnings": validation["warnings"],
        "exact_decline": payload,
        "prepared_action": prepared,
        "external_action_taken": False,
    }


async def _page_text(page) -> str:
    body = await page.query_selector("body")
    return ((await body.text_content()) if body else "") or ""


async def _click(page, element) -> None:
    try:
        await element.click()
    except Exception:
        await page.evaluate("element => element.click()", element)


def _normalise(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


async def _choose_decline_reason(page, reason: str) -> bool:
    select = await page.query_selector(
        '[role="dialog"] select[name*="reason"], select[data-test*="decline-reason"]'
    )
    if select:
        try:
            await select.select_option(label=reason)
            return True
        except Exception:
            return False

    expected = _normalise(reason).casefold()
    candidates = await page.query_selector_all(
        '[role="dialog"] label, [role="dialog"] [role="radio"], '
        '[role="dialog"] [role="option"], [data-test*="decline-reason"] label'
    )
    for candidate in candidates:
        text = _normalise((await candidate.text_content()) or "")
        if text.casefold() == expected:
            await _click(page, candidate)
            return True
    return False


async def decline_invitation(params: DeclineInvitationParams) -> dict[str, Any]:
    """Decline an invitation only after exact-payload owner approval."""

    payload = invitation_decline_payload(params)
    blocked = approval_gate(
        "decline_invitation",
        payload,
        approved=params.approved,
        approval_sha256=params.approval_sha256,
        action_id=params.action_id,
    )
    if blocked:
        return blocked

    browser = get_browser()
    await browser.ensure_logged_in()
    async with browser.operation() as page:
        return await _decline_invitation_on_page(params, page)


async def _decline_invitation_on_page(params: DeclineInvitationParams, page) -> dict[str, Any]:
    """Decline while the browser operation lock is held."""

    await page.goto(params.invitation_url, wait_until="networkidle")

    initial_text = await _page_text(page)
    already_declined = re.search(
        r"you (?:have )?declined (?:this |the )?invitation|invitation (?:was |has been )?declined",
        initial_text,
        re.I,
    )
    if already_declined:
        return {
            "status": "already_declined",
            "owner_system_readback": {
                "confirmed": True,
                "evidence": already_declined.group(0),
                "url": str(getattr(page, "url", params.invitation_url)),
            },
            "external_action_taken": False,
        }

    decline_button = await page.query_selector(
        '[data-test="decline-button"], button:has-text("Decline invitation"), '
        'button:has-text("Decline Interview"), button:has-text("Decline")'
    )
    if not decline_button:
        return {
            "status": "error",
            "message": "Decline invitation button not found.",
            "external_action_taken": False,
        }
    await _click(page, decline_button)

    if not await _choose_decline_reason(page, params.reason):
        return {
            "status": "live_form_mismatch",
            "message": "The exact approved decline reason was not available; nothing was confirmed.",
            "external_action_taken": False,
        }

    if params.note:
        note_input = await page.query_selector(
            '[role="dialog"] textarea, textarea[name*="message"], textarea[name*="reason"]'
        )
        if not note_input:
            return {
                "status": "live_form_mismatch",
                "message": "The approved note field was not available; nothing was confirmed.",
                "external_action_taken": False,
            }
        await note_input.fill(params.note)

    block_checkbox = await page.query_selector(
        'input[name*="block"], input[data-test*="block"], '
        'label:has-text("Block") input[type="checkbox"]'
    )
    if block_checkbox:
        try:
            if await block_checkbox.is_checked():
                await block_checkbox.uncheck()
        except Exception:
            return {
                "status": "error",
                "message": "Future-invitation blocking could not be verified as disabled.",
                "external_action_taken": False,
            }

    confirm_button = await page.query_selector(
        '[data-test="confirm-decline"], [role="dialog"] button:has-text("Decline invitation"), '
        '[role="dialog"] button:has-text("Decline")'
    )
    if not confirm_button:
        return {
            "status": "error",
            "message": "Final decline confirmation button not found.",
            "external_action_taken": False,
        }
    await _click(page, confirm_button)

    for _ in range(20):
        text = await _page_text(page)
        evidence = re.search(
            r"you (?:have )?declined (?:this |the )?invitation|invitation (?:was |has been )?declined",
            text,
            re.I,
        )
        if evidence:
            return {
                "status": "declined",
                "message": "Invitation decline read back from Upwork",
                "owner_system_readback": {
                    "confirmed": True,
                    "evidence": evidence.group(0),
                    "url": str(getattr(page, "url", params.invitation_url)),
                },
                "external_action_taken": True,
            }
        await asyncio.sleep(0.5)

    return {
        "status": "unknown",
        "message": "Upwork did not confirm the decline; do not retry automatically.",
        "owner_system_readback": {
            "confirmed": False,
            "url": str(getattr(page, "url", params.invitation_url)),
        },
        "external_action_taken": True,
    }
