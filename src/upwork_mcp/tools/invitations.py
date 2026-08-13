"""Approval-gated Upwork invitation actions."""

from __future__ import annotations

import asyncio
import re
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator

from ..browser.client import get_browser
from ..prepared_actions import prepare_action
from ..strategy import validate_upwork_copy
from .proposals import StrictToolModel, approval_gate, validate_upwork_url

_INVITATION_PATH = re.compile(
    r"^/nx/proposals/interview/uid/(?P<invitation_id>[0-9]{19})/?$"
)
_INVITATION_DISCOVERY_PATH = re.compile(
    r"^/nx/proposals/interview/uid/(?P<invitation_id>[0-9]{19})(?:/accept)?/?$"
)


def parse_invitation_url(value: str) -> tuple[str, str]:
    """Return one canonical pending-invitation route and its identity."""

    candidate = validate_upwork_url(value)
    match = _INVITATION_PATH.fullmatch(urlparse(candidate).path)
    if not match:
        raise ValueError(
            "URL must point to one invitation at /nx/proposals/interview/uid/<invitation_id>"
        )
    invitation_id = match.group("invitation_id")
    return f"https://www.upwork.com/nx/proposals/interview/uid/{invitation_id}", invitation_id


def validate_invitation_url(value: str) -> str:
    return parse_invitation_url(value)[0]


def _invitation_from_discovery_link(value: str) -> tuple[str, str]:
    candidate = validate_upwork_url(value)
    match = _INVITATION_DISCOVERY_PATH.fullmatch(urlparse(candidate).path)
    if not match:
        raise ValueError("Not an individual invitation discovery link")
    invitation_id = match.group("invitation_id")
    return f"https://www.upwork.com/nx/proposals/interview/uid/{invitation_id}", invitation_id


class InvitationsParams(StrictToolModel):
    """Read-only invitation-list parameters."""

    limit: int = Field(default=20, ge=1, le=50)


class DeclineInvitationParams(StrictToolModel):
    """Exact owner-approved invitation decline."""

    invitation_url: str = Field(description="Full Upwork invitation URL")
    invitation_id: str = Field(
        pattern=r"^[0-9]{19}$",
        description="Exact 19-digit invitation identity",
    )
    job_title: str = Field(min_length=1, max_length=1000, description="Live invitation job title")
    invitation_status: str = Field(
        min_length=1,
        max_length=200,
        description="Live invitation status bound at preparation",
    )
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

    _validate_invitation_url = field_validator("invitation_url")(validate_invitation_url)

    @field_validator("invitation_status", "job_title")
    @classmethod
    def _identity_text_must_not_be_blank(cls, value: str) -> str:
        normalized = _normalise(value)
        if not normalized:
            raise ValueError("Invitation identity fields cannot be blank")
        return normalized

    @field_validator("note")
    @classmethod
    def _note_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Use null instead of a blank decline note")
        return value

    @model_validator(mode="after")
    def _invitation_id_must_match_url(self) -> DeclineInvitationParams:
        _, route_id = parse_invitation_url(self.invitation_url)
        if route_id != self.invitation_id:
            raise ValueError("invitation_id does not match the individual invitation URL")
        return self


def invitation_decline_payload(params: DeclineInvitationParams) -> dict[str, Any]:
    return {
        "invitation_url": params.invitation_url,
        "invitation_id": params.invitation_id,
        "job_title": params.job_title,
        "invitation_status": params.invitation_status,
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
            raw_url = href if href.startswith("http") else f"https://www.upwork.com{href}"
            try:
                url, invitation_id = _invitation_from_discovery_link(raw_url)
            except ValueError:
                continue
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
                    "invitation_id": invitation_id,
                    "summary": summary[:2_000],
                }
            )
            if len(invitations) >= params.limit:
                break
    return invitations


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


async def _read_invitation_identity(page, invitation_url: str) -> dict[str, str] | None:
    """Read the exact invitation route, title, and live state from Upwork."""

    canonical_url, invitation_id = parse_invitation_url(invitation_url)
    await page.goto(canonical_url, wait_until="networkidle")
    try:
        live_url, live_id = parse_invitation_url(str(getattr(page, "url", "")))
    except ValueError:
        return None
    if live_id != invitation_id:
        return None

    title_el = await page.query_selector('[data-test="job-title"], h1, .job-title')
    job_title = _normalise((await title_el.text_content()) or "") if title_el else ""
    text = await _page_text(page)
    if re.search(
        r"you (?:have )?declined (?:this |the )?invitation|invitation (?:was |has been )?declined",
        text,
        re.I,
    ):
        invitation_status = "declined"
    elif re.search(r"invitation (?:was |has been )?accepted|you accepted (?:this |the )?invitation", text, re.I):
        invitation_status = "accepted"
    elif re.search(r"invitation (?:has )?expired|invitation (?:is )?closed", text, re.I):
        invitation_status = "closed"
    elif re.search(
        r"pending invitation|invitation to (?:apply|interview)|"
        r"invited you to (?:apply|interview)|respond to (?:this |the )?invitation",
        text,
        re.I,
    ):
        invitation_status = "pending"
    else:
        status_el = await page.query_selector('[data-test*="invitation-status"], .invitation-status')
        invitation_status = (
            _normalise((await status_el.text_content()) or "") if status_el else ""
        )

    if not job_title or not invitation_status:
        return None
    return {
        "invitation_url": live_url,
        "invitation_id": invitation_id,
        "job_title": job_title,
        "invitation_status": invitation_status,
    }


async def prepare_invitation_decline_from_live(
    invitation_url: str,
    *,
    reason: Literal["Not interested in work described"] = "Not interested in work described",
    note: str | None = None,
) -> dict[str, Any]:
    """Read one invitation and bind its identity before creating approval state."""

    canonical_url, _ = parse_invitation_url(invitation_url)
    browser = get_browser()
    await browser.ensure_logged_in()
    async with browser.operation() as page:
        identity = await _read_invitation_identity(page, canonical_url)

    errors: list[str] = []
    if identity is None:
        errors.append("The invitation identity and live status could not be read back from Upwork")
    elif identity["invitation_status"].casefold() != "pending":
        errors.append(f"The invitation is not pending: {identity['invitation_status']}")

    payload: dict[str, Any] | None = None
    prepared = None
    validation: dict[str, Any] = (
        validate_upwork_copy(note or "No client note")
        if note
        else {"valid": True, "errors": [], "warnings": []}
    )
    errors.extend(str(error) for error in validation.get("errors", []))
    if identity is not None:
        params = DeclineInvitationParams(
            invitation_url=identity["invitation_url"],
            invitation_id=identity["invitation_id"],
            job_title=identity["job_title"],
            invitation_status=identity["invitation_status"],
            reason=reason,
            note=note,
        )
        payload = invitation_decline_payload(params)
        if not errors:
            prepared = prepare_action("invitation_decline", payload)

    return {
        "ready_for_owner_approval": not errors,
        "errors": list(dict.fromkeys(errors)),
        "warnings": validation["warnings"],
        "current_invitation": identity,
        "exact_decline": payload,
        "prepared_action": prepared,
        "external_action_taken": False,
    }


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

    live_identity = await _read_invitation_identity(page, params.invitation_url)
    approved_identity = {
        "invitation_url": params.invitation_url,
        "invitation_id": params.invitation_id,
        "job_title": _normalise(params.job_title),
        "invitation_status": _normalise(params.invitation_status),
    }
    if live_identity and live_identity["invitation_status"].casefold() == "declined":
        return {
            "status": "already_declined",
            "owner_system_readback": {
                "confirmed": True,
                "invitation_identity": live_identity,
                "url": live_identity["invitation_url"],
            },
            "external_action_taken": False,
        }
    if live_identity != approved_identity:
        return {
            "status": "live_identity_mismatch",
            "message": (
                "The live invitation identity or status differs from the approved payload; "
                "nothing was declined. Prepare the current invitation again before new approval."
            ),
            "approved_invitation_identity": approved_identity,
            "live_invitation_identity": live_identity,
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
            readback_identity = await _read_invitation_identity(page, params.invitation_url)
            same_target = bool(
                readback_identity
                and readback_identity["invitation_id"] == params.invitation_id
                and readback_identity["job_title"] == approved_identity["job_title"]
                and readback_identity["invitation_status"].casefold() == "declined"
            )
            if not same_target:
                return {
                    "status": "unknown",
                    "message": (
                        "Upwork showed decline text but the same invitation identity and declined "
                        "status could not be read back; do not retry automatically."
                    ),
                    "owner_system_readback": {
                        "confirmed": False,
                        "evidence": evidence.group(0),
                        "invitation_identity": readback_identity,
                        "url": str(getattr(page, "url", params.invitation_url)),
                    },
                    "external_action_taken": True,
                }
            return {
                "status": "declined",
                "message": "Invitation decline read back from Upwork",
                "owner_system_readback": {
                    "confirmed": True,
                    "evidence": evidence.group(0),
                    "invitation_identity": readback_identity,
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
            "invitation_identity": approved_identity,
            "url": str(getattr(page, "url", params.invitation_url)),
        },
        "external_action_taken": True,
    }
