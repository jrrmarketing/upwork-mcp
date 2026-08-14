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

_INVITATION_TITLE_SELECTOR = '[data-test="job-title"], .job-title, main h1'
_INITIAL_DECLINE_SELECTOR = (
    '[data-test="decline-button"], button, [role="button"]'
)
_DIALOG_SELECTOR = '[role="dialog"]'
_DIALOG_HEADING_SELECTOR = 'h1, h2, h3, [role="heading"], [data-test*="title"]'
_REASON_SELECT_SELECTOR = (
    'select[name*="reason" i], select[data-test*="decline-reason" i]'
)
_REASON_RADIO_SELECTOR = 'label, [role="radio"]'
_NOTE_SELECTOR = (
    'textarea[data-test*="decline-note" i], textarea[name*="message" i], '
    'textarea[name*="note" i], textarea'
)
_BLOCK_SELECTOR = (
    'input[type="checkbox"][name*="block" i], '
    'input[type="checkbox"][data-test*="block" i], '
    '[role="checkbox"][data-test*="block" i], '
    '[role="checkbox"][aria-label*="block" i]'
)
_BLOCK_NOT_APPLICABLE_SELECTOR = (
    '[data-test="block-not-applicable"], '
    '[data-test="block-client-not-applicable"]'
)
_DIALOG_BUTTON_SELECTOR = 'button, [role="button"]'

_DECLINE_LABELS = {"decline", "decline invitation"}
_INITIAL_DECLINE_LABELS = _DECLINE_LABELS | {"decline interview"}


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


async def _visible_elements(scope, selector: str) -> list[Any] | None:
    """Completely enumerate a selector and keep only visible elements."""

    try:
        candidates = await scope.query_selector_all(selector)
    except Exception:
        return None
    visible: list[Any] = []
    for candidate in candidates:
        try:
            if await candidate.is_visible():
                visible.append(candidate)
        except Exception:
            return None
    return visible


async def _current_invitation_identity(
    page,
    invitation_url: str,
) -> dict[str, str] | None:
    """Read the current invitation without navigating away from an open dialog."""

    canonical_url, invitation_id = parse_invitation_url(invitation_url)
    try:
        live_url, live_id = parse_invitation_url(str(getattr(page, "url", "")))
    except ValueError:
        return None
    if live_id != invitation_id or live_url != canonical_url:
        return None

    title_elements = await _visible_elements(page, _INVITATION_TITLE_SELECTOR)
    if title_elements is None:
        return None
    titles: list[str] = []
    for title_element in title_elements:
        try:
            title = _normalise((await title_element.text_content()) or "")
        except Exception:
            return None
        if title and title not in titles:
            titles.append(title)
    if len(titles) != 1:
        return None

    try:
        text = await _page_text(page)
    except Exception:
        return None
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

    if not invitation_status:
        return None
    return {
        "invitation_url": live_url,
        "invitation_id": invitation_id,
        "job_title": titles[0],
        "invitation_status": invitation_status,
    }


async def _read_invitation_identity(page, invitation_url: str) -> dict[str, str] | None:
    """Navigate to and read the exact invitation route, title, and live state."""

    canonical_url, _ = parse_invitation_url(invitation_url)
    await page.goto(canonical_url, wait_until="networkidle")
    return await _current_invitation_identity(page, canonical_url)


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
async def _element_text(element) -> str | None:
    try:
        return _normalise((await element.text_content()) or "")
    except Exception:
        return None


async def _element_attribute(element, name: str) -> str | None:
    try:
        return (await element.get_attribute(name) or "").strip()
    except Exception:
        return None


async def _exact_initial_decline_control(page) -> tuple[Any | None, str | None]:
    controls = await _visible_elements(page, _INITIAL_DECLINE_SELECTOR)
    if controls is None:
        return None, "The invitation action controls could not be completely enumerated"
    exact: list[Any] = []
    for control in controls:
        text = await _element_text(control)
        data_test = await _element_attribute(control, "data-test")
        if text is None or data_test is None:
            return None, "An invitation action control could not be completely read"
        if text.casefold() in _INITIAL_DECLINE_LABELS or (
            data_test.casefold() == "decline-button"
            and text.casefold() in _INITIAL_DECLINE_LABELS
        ):
            exact.append(control)
    if len(exact) != 1:
        return None, "Exactly one visible exact Decline invitation control was not found"
    try:
        if not await exact[0].is_enabled():
            return None, "The exact Decline invitation control was disabled"
    except Exception:
        return None, "The exact Decline invitation control state could not be read"
    return exact[0], None


async def _is_exact_decline_dialog(dialog) -> bool | None:
    data_test = await _element_attribute(dialog, "data-test")
    aria_label = await _element_attribute(dialog, "aria-label")
    if data_test is None or aria_label is None:
        return None
    if data_test.casefold() in {
        "decline-invitation-dialog",
        "decline-invitation-modal",
    } or aria_label.casefold() == "decline invitation":
        return True

    headings = await _visible_elements(dialog, _DIALOG_HEADING_SELECTOR)
    if headings is None:
        return None
    for heading in headings:
        text = await _element_text(heading)
        if text is None:
            return None
        if text.casefold() == "decline invitation":
            return True
    return False


async def _exact_decline_dialog(page) -> tuple[Any | None, str | None]:
    dialogs = await _visible_elements(page, _DIALOG_SELECTOR)
    if dialogs is None:
        return None, "Visible dialogs could not be completely enumerated"
    exact: list[Any] = []
    for dialog in dialogs:
        is_exact = await _is_exact_decline_dialog(dialog)
        if is_exact is None:
            return None, "A visible dialog identity could not be completely read"
        if is_exact:
            exact.append(dialog)
    if len(exact) != 1:
        return None, "Exactly one visible exact Decline invitation dialog was not found"
    return exact[0], None


async def _wait_for_exact_decline_dialog(page) -> tuple[Any | None, str | None]:
    last_error = "Exactly one visible exact Decline invitation dialog was not found"
    for _ in range(20):
        dialog, error = await _exact_decline_dialog(page)
        if dialog is not None:
            return dialog, None
        last_error = error or last_error
        await asyncio.sleep(0.1)
    return None, last_error


async def _selected_option_label(select) -> str | None:
    try:
        selected = await select.query_selector_all("option:checked")
    except Exception:
        return None
    if len(selected) != 1:
        return None
    return await _element_text(selected[0])


async def _radio_checked_state(candidate) -> bool | None:
    try:
        role = (await candidate.get_attribute("role") or "").casefold()
        if role == "radio":
            return bool(await candidate.is_checked())
        nested = await candidate.query_selector('input[type="radio"], [role="radio"]')
        if nested is None:
            return None
        return bool(await nested.is_checked())
    except Exception:
        return None


async def _exact_reason_control(
    dialog,
    reason: str,
) -> tuple[str | None, Any | None, str | None]:
    selects = await _visible_elements(dialog, _REASON_SELECT_SELECTOR)
    radio_candidates = await _visible_elements(dialog, _REASON_RADIO_SELECTOR)
    if selects is None or radio_candidates is None:
        return None, None, "The decline-reason controls could not be completely enumerated"

    expected = _normalise(reason).casefold()
    exact_radios: list[Any] = []
    for candidate in radio_candidates:
        text = await _element_text(candidate)
        aria_label = await _element_attribute(candidate, "aria-label")
        if text is None or aria_label is None:
            return None, None, "A decline-reason control could not be completely read"
        if text.casefold() == expected or aria_label.casefold() == expected:
            exact_radios.append(candidate)

    if len(selects) == 1 and not exact_radios:
        return "select", selects[0], None
    if not selects and len(exact_radios) == 1:
        return "radio", exact_radios[0], None
    return None, None, "Exactly one control for the approved decline reason was not found"


async def _set_exact_decline_reason(dialog, reason: str) -> tuple[bool, str | None]:
    kind, control, error = await _exact_reason_control(dialog, reason)
    if control is None or kind is None:
        return False, error
    try:
        if kind == "select":
            await control.select_option(label=reason)
            selected = await _selected_option_label(control)
            if selected != _normalise(reason):
                return False, "The approved decline reason did not read back exactly"
        else:
            await _click(dialog, control)
            if await _radio_checked_state(control) is not True:
                return False, "The approved decline reason did not read back as selected"
    except Exception:
        return False, "The approved decline reason could not be selected and read back"
    return True, None


async def _approved_reason_is_selected(dialog, reason: str) -> tuple[bool, str | None]:
    kind, control, error = await _exact_reason_control(dialog, reason)
    if control is None or kind is None:
        return False, error
    if kind == "select":
        selected = await _selected_option_label(control)
        if selected != _normalise(reason):
            return False, "The approved decline reason changed before confirmation"
        return True, None
    if await _radio_checked_state(control) is not True:
        return False, "The approved decline reason changed before confirmation"
    return True, None


async def _exact_note_control(dialog) -> tuple[Any | None, str | None]:
    notes = await _visible_elements(dialog, _NOTE_SELECTOR)
    if notes is None:
        return None, "The decline-note fields could not be completely enumerated"
    if len(notes) != 1:
        return None, "Exactly one visible decline-note field was not found"
    return notes[0], None


async def _note_value(note_control) -> str | None:
    try:
        return await note_control.input_value()
    except Exception:
        return None


async def _set_or_verify_exact_note(dialog, note: str | None) -> tuple[bool, str | None]:
    note_control, error = await _exact_note_control(dialog)
    if note_control is None:
        return False, error
    expected = note or ""
    if note is not None:
        try:
            await note_control.fill(note)
        except Exception:
            return False, "The approved decline note could not be filled"
    if await _note_value(note_control) != expected:
        return False, "The decline note did not read back byte-for-byte as approved"
    return True, None


async def _approved_note_is_current(dialog, note: str | None) -> tuple[bool, str | None]:
    note_control, error = await _exact_note_control(dialog)
    if note_control is None:
        return False, error
    if await _note_value(note_control) != (note or ""):
        return False, "The decline note changed before confirmation"
    return True, None


async def _dialog_says_block_not_applicable(dialog) -> bool | None:
    markers = await _visible_elements(dialog, _BLOCK_NOT_APPLICABLE_SELECTOR)
    if markers is None:
        return None
    if len(markers) > 1:
        return None
    if len(markers) == 1:
        return True
    text = await _element_text(dialog)
    if text is None:
        return None
    return bool(
        re.search(
            r"(?:blocking (?:this )?client|blocking future invitations|"
            r"block(?:ing)? future invitations) (?:is |are )?not "
            r"(?:available|applicable)(?: for this invitation)?",
            text,
            re.I,
        )
    )


async def _verify_blocking_disabled(
    dialog,
    *,
    repair_checked_state: bool,
) -> tuple[bool, str | None]:
    controls = await _visible_elements(dialog, _BLOCK_SELECTOR)
    if controls is None:
        return False, "Block-client controls could not be completely enumerated"
    if len(controls) > 1:
        return False, "More than one visible block-client checkbox was found"
    if not controls:
        not_applicable = await _dialog_says_block_not_applicable(dialog)
        if not_applicable is True:
            return True, None
        return False, "No block-client checkbox or explicit not-applicable state was found"

    checkbox = controls[0]
    try:
        checked = bool(await checkbox.is_checked())
        if checked and repair_checked_state:
            await checkbox.uncheck()
            checked = bool(await checkbox.is_checked())
    except Exception:
        return False, "Future-invitation blocking could not be read back"
    if checked:
        return False, "Future-invitation blocking was not proved disabled"
    return True, None


async def _exact_final_decline_control(dialog) -> tuple[Any | None, str | None]:
    controls = await _visible_elements(dialog, _DIALOG_BUTTON_SELECTOR)
    if controls is None:
        return None, "Dialog buttons could not be completely enumerated"
    exact: list[Any] = []
    for control in controls:
        text = await _element_text(control)
        if text is None:
            return None, "A dialog button label could not be read"
        if text.casefold() in _DECLINE_LABELS:
            exact.append(control)
    if len(exact) != 1:
        return None, "Exactly one dialog-scoped exact Decline control was not found"
    try:
        if not await exact[0].is_enabled():
            return None, "The exact dialog-scoped Decline control was disabled"
    except Exception:
        return None, "The exact dialog-scoped Decline control state could not be read"
    return exact[0], None


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

    decline_button, decline_error = await _exact_initial_decline_control(page)
    if decline_button is None:
        return {
            "status": "live_form_mismatch",
            "message": decline_error,
            "external_action_taken": False,
        }
    await _click(page, decline_button)

    dialog, dialog_error = await _wait_for_exact_decline_dialog(page)
    if dialog is None:
        return {
            "status": "live_form_mismatch",
            "message": dialog_error,
            "external_action_taken": False,
        }

    reason_ok, reason_error = await _set_exact_decline_reason(dialog, params.reason)
    if not reason_ok:
        return {
            "status": "live_form_mismatch",
            "message": reason_error,
            "external_action_taken": False,
        }

    note_ok, note_error = await _set_or_verify_exact_note(dialog, params.note)
    if not note_ok:
        return {
            "status": "live_form_mismatch",
            "message": note_error,
            "external_action_taken": False,
        }

    block_ok, block_error = await _verify_blocking_disabled(
        dialog,
        repair_checked_state=True,
    )
    if not block_ok:
        return {
            "status": "live_form_mismatch",
            "message": block_error,
            "external_action_taken": False,
        }

    # Resolve the exact dialog and its consequential control again, then perform every
    # approved-state readback with no further selector resolution before the click.
    dialog, dialog_error = await _exact_decline_dialog(page)
    if dialog is None:
        return {
            "status": "live_form_mismatch",
            "message": dialog_error,
            "external_action_taken": False,
        }
    confirm_button, confirm_error = await _exact_final_decline_control(dialog)
    if confirm_button is None:
        return {
            "status": "live_form_mismatch",
            "message": confirm_error,
            "external_action_taken": False,
        }

    final_identity = await _current_invitation_identity(page, params.invitation_url)
    if final_identity != approved_identity:
        return {
            "status": "live_identity_mismatch",
            "message": (
                "The invitation identity or status changed before confirmation; "
                "nothing was declined. Prepare the current invitation again."
            ),
            "approved_invitation_identity": approved_identity,
            "live_invitation_identity": final_identity,
            "external_action_taken": False,
        }

    reason_ok, reason_error = await _approved_reason_is_selected(dialog, params.reason)
    if not reason_ok:
        return {
            "status": "live_form_mismatch",
            "message": reason_error,
            "external_action_taken": False,
        }
    note_ok, note_error = await _approved_note_is_current(dialog, params.note)
    if not note_ok:
        return {
            "status": "live_form_mismatch",
            "message": note_error,
            "external_action_taken": False,
        }
    block_ok, block_error = await _verify_blocking_disabled(
        dialog,
        repair_checked_state=False,
    )
    if not block_ok:
        return {
            "status": "live_form_mismatch",
            "message": block_error,
            "external_action_taken": False,
        }

    await _click(dialog, confirm_button)

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
