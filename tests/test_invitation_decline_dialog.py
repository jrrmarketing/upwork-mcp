"""Adversarial offline tests for the exact invitation-decline dialog."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest

from upwork_mcp.prepared_actions import approve_action, prepare_action
from upwork_mcp.tools import invitations


class _Element:
    def __init__(
        self,
        text: str = "",
        *,
        visible: bool = True,
        enabled: bool = True,
        attributes: dict[str, str] | None = None,
    ) -> None:
        self.text = text
        self.visible = visible
        self.enabled = enabled
        self.attributes = attributes or {}

    async def text_content(self) -> str:
        return self.text

    async def is_visible(self) -> bool:
        return self.visible

    async def is_enabled(self) -> bool:
        return self.enabled

    async def get_attribute(self, name: str) -> str | None:
        return self.attributes.get(name)

    async def query_selector(self, _selector: str):
        return None

    async def query_selector_all(self, _selector: str) -> list[Any]:
        return []


class _Button(_Element):
    def __init__(
        self,
        text: str,
        callback: Callable[[], None] | None = None,
        *,
        visible: bool = True,
    ) -> None:
        super().__init__(text, visible=visible)
        self.callback = callback or (lambda: None)

    async def click(self) -> None:
        self.callback()


class _HidesBeforeClickButton(_Button):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.visibility_reads = 0

    async def is_visible(self) -> bool:
        self.visibility_reads += 1
        return self.visibility_reads == 1


class _Option(_Element):
    pass


class _Select(_Element):
    def __init__(
        self,
        *,
        selected: str = "Something else",
        retain_selection: bool = False,
        visible: bool = True,
    ) -> None:
        super().__init__(visible=visible)
        self.selected = selected
        self.retain_selection = retain_selection

    async def select_option(self, *, label: str) -> None:
        if not self.retain_selection:
            self.selected = label

    async def query_selector_all(self, selector: str) -> list[Any]:
        if selector == "option:checked":
            return [_Option(self.selected)]
        return []


class _Note(_Element):
    def __init__(self, value: str = "", *, visible: bool = True) -> None:
        super().__init__(visible=visible)
        self.value = value

    async def fill(self, value: str) -> None:
        self.value = value

    async def input_value(self) -> str:
        return self.value


class _Checkbox(_Element):
    def __init__(
        self,
        *,
        checked: bool = False,
        retain_checked: bool = False,
        visible: bool = True,
    ) -> None:
        super().__init__(visible=visible)
        self.checked = checked
        self.retain_checked = retain_checked
        self.uncheck_count = 0

    async def is_checked(self) -> bool:
        return self.checked

    async def uncheck(self) -> None:
        self.uncheck_count += 1
        if not self.retain_checked:
            self.checked = False


class _Dialog(_Element):
    def __init__(
        self,
        *,
        reason_selects: list[_Select] | None = None,
        notes: list[_Note] | None = None,
        blocks: list[_Checkbox] | None = None,
        buttons: list[_Button] | None = None,
        exact_heading: bool = True,
        explicit_block_not_applicable: bool = False,
        visible: bool = False,
    ) -> None:
        dialog_text = "Decline invitation"
        if explicit_block_not_applicable:
            dialog_text += " Blocking future invitations is not applicable"
        super().__init__(dialog_text, visible=visible)
        self.reason_selects = reason_selects or [_Select()]
        self.notes = notes or [_Note()]
        self.blocks = blocks if blocks is not None else [_Checkbox()]
        self.buttons = buttons or []
        self.headings = [_Element("Decline invitation")] if exact_heading else [_Element("Confirm")]
        self.explicit_block_not_applicable = explicit_block_not_applicable

    async def query_selector_all(self, selector: str) -> list[Any]:
        if selector == invitations._DIALOG_HEADING_SELECTOR:
            return self.headings
        if selector == invitations._REASON_SELECT_SELECTOR:
            return self.reason_selects
        if selector == invitations._REASON_RADIO_SELECTOR:
            return []
        if selector == invitations._NOTE_SELECTOR:
            return self.notes
        if selector == invitations._BLOCK_SELECTOR:
            return self.blocks
        if selector == invitations._BLOCK_NOT_APPLICABLE_SELECTOR:
            return (
                [_Element("Blocking not applicable")]
                if self.explicit_block_not_applicable
                else []
            )
        if selector == invitations._DIALOG_BUTTON_SELECTOR:
            return self.buttons
        return []


class _InvitationPage:
    def __init__(
        self,
        dialog: _Dialog,
        *,
        update_scoped_status_on_confirm: bool = True,
    ) -> None:
        self.url = "https://www.upwork.com/nx/proposals/interview/uid/3333333333333333333"
        self.title = "Agency Google Ads support"
        self.body = "Pending invitation"
        self.status_elements = [_Element("pending")]
        self.dialogs = [dialog]
        self.final_clicks = 0
        self.title_reads = 0
        self.before_final_identity_read: Callable[[], None] | None = None
        self.initial_button = _Button("Decline invitation", self._open_dialog)

        def confirm() -> None:
            self.final_clicks += 1
            self.body = "You have declined this invitation"
            if update_scoped_status_on_confirm:
                self.status_elements[0].text = "declined"
            dialog.visible = False

        if not dialog.buttons:
            dialog.buttons = [_Button("Decline invitation", confirm)]
        else:
            for button in dialog.buttons:
                if button.text.casefold() in invitations._DECLINE_LABELS:
                    button.callback = confirm

    def _open_dialog(self) -> None:
        self.dialogs[0].visible = True

    async def goto(self, url: str, **_kwargs) -> None:
        self.url = url

    async def query_selector(self, selector: str):
        if selector == "body":
            return _Element(self.body)
        if "invitation-status" in selector or selector == ".invitation-status":
            return None
        return None

    async def query_selector_all(self, selector: str) -> list[Any]:
        if selector == invitations._INVITATION_TITLE_SELECTOR:
            self.title_reads += 1
            if self.title_reads == 2 and self.before_final_identity_read:
                self.before_final_identity_read()
            return [_Element(self.title)]
        if selector == invitations._INVITATION_STATUS_SELECTOR:
            return self.status_elements
        if selector == invitations._INITIAL_DECLINE_SELECTOR:
            return [self.initial_button]
        if selector == invitations._DIALOG_SELECTOR:
            return self.dialogs
        return []


class _Browser:
    def __init__(self, page: _InvitationPage) -> None:
        self.page = page

    async def ensure_logged_in(self) -> None:
        return None

    @asynccontextmanager
    async def operation(self):
        yield self.page


def _params(*, note: str | None = None) -> invitations.DeclineInvitationParams:
    return invitations.DeclineInvitationParams(
        invitation_url=(
            "https://www.upwork.com/nx/proposals/interview/uid/3333333333333333333"
        ),
        invitation_id="3333333333333333333",
        job_title="Agency Google Ads support",
        invitation_status="pending",
        reason="Not interested in work described",
        note=note,
    )


@pytest.mark.asyncio
async def test_decline_uses_exact_dialog_states_and_owner_readback() -> None:
    checkbox = _Checkbox(checked=True)
    dialog = _Dialog(blocks=[checkbox])
    page = _InvitationPage(dialog)

    result = await invitations._decline_invitation_on_page(
        _params(note="Consultancy projects only, rather than a full-time placement."),
        page,
    )

    assert result["status"] == "declined"
    assert result["owner_system_readback"]["confirmed"] is True
    assert result["external_action_taken"] is True
    assert page.final_clicks == 1
    assert checkbox.checked is False
    assert checkbox.uncheck_count == 1
    assert dialog.notes[0].value == "Consultancy projects only, rather than a full-time placement."


@pytest.mark.asyncio
async def test_silent_reason_retention_blocks_final_decline() -> None:
    dialog = _Dialog(reason_selects=[_Select(retain_selection=True)])
    page = _InvitationPage(dialog)

    result = await invitations._decline_invitation_on_page(_params(), page)

    assert result["status"] == "live_form_mismatch"
    assert "reason" in result["message"].casefold()
    assert result["external_action_taken"] is False
    assert page.final_clicks == 0


@pytest.mark.asyncio
async def test_absent_approved_note_must_read_back_exactly_empty() -> None:
    dialog = _Dialog(notes=[_Note("An old unsent note")])
    page = _InvitationPage(dialog)

    result = await invitations._decline_invitation_on_page(_params(note=None), page)

    assert result["status"] == "live_form_mismatch"
    assert "note" in result["message"].casefold()
    assert page.final_clicks == 0


@pytest.mark.asyncio
async def test_unrelated_confirm_is_never_used_as_decline_confirmation() -> None:
    dialog = _Dialog(buttons=[_Button("Confirm"), _Button("Cancel")])
    page = _InvitationPage(dialog)

    result = await invitations._decline_invitation_on_page(_params(), page)

    assert result["status"] == "live_form_mismatch"
    assert "dialog-scoped exact decline" in result["message"].casefold()
    assert page.final_clicks == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("duplicate_kind", ["reason", "note", "block", "confirm"])
async def test_duplicate_visible_dialog_controls_block_confirmation(
    duplicate_kind: str,
) -> None:
    dialog = _Dialog()
    if duplicate_kind == "reason":
        dialog.reason_selects.append(_Select())
    elif duplicate_kind == "note":
        dialog.notes.append(_Note())
    elif duplicate_kind == "block":
        dialog.blocks.append(_Checkbox())
    else:
        dialog.buttons = [_Button("Decline"), _Button("Decline invitation")]
    page = _InvitationPage(dialog)

    result = await invitations._decline_invitation_on_page(_params(), page)

    assert result["status"] == "live_form_mismatch"
    assert result["external_action_taken"] is False
    assert page.final_clicks == 0


@pytest.mark.asyncio
async def test_hidden_duplicate_controls_do_not_replace_unique_visible_controls() -> None:
    dialog = _Dialog(
        reason_selects=[_Select(), _Select(visible=False)],
        notes=[_Note(), _Note(visible=False)],
        blocks=[_Checkbox(), _Checkbox(visible=False)],
    )
    page = _InvitationPage(dialog)
    dialog.buttons.append(_Button("Decline", visible=False))

    result = await invitations._decline_invitation_on_page(_params(), page)

    assert result["status"] == "declined"
    assert page.final_clicks == 1


@pytest.mark.asyncio
async def test_duplicate_exact_visible_dialogs_block_confirmation() -> None:
    page = _InvitationPage(_Dialog())
    page.dialogs.append(_Dialog(visible=True))

    result = await invitations._decline_invitation_on_page(_params(), page)

    assert result["status"] == "live_form_mismatch"
    assert "exact decline invitation dialog" in result["message"].casefold()
    assert page.final_clicks == 0


@pytest.mark.asyncio
async def test_missing_block_checkbox_requires_explicit_not_applicable_state() -> None:
    page = _InvitationPage(_Dialog(blocks=[]))

    result = await invitations._decline_invitation_on_page(_params(), page)

    assert result["status"] == "live_form_mismatch"
    assert "not-applicable" in result["message"].casefold()
    assert page.final_clicks == 0


@pytest.mark.asyncio
async def test_explicit_block_not_applicable_state_allows_no_checkbox() -> None:
    page = _InvitationPage(
        _Dialog(blocks=[], explicit_block_not_applicable=True)
    )

    result = await invitations._decline_invitation_on_page(_params(), page)

    assert result["status"] == "declined"
    assert page.final_clicks == 1


@pytest.mark.asyncio
async def test_silent_block_checkbox_retention_blocks_confirmation() -> None:
    page = _InvitationPage(
        _Dialog(blocks=[_Checkbox(checked=True, retain_checked=True)])
    )

    result = await invitations._decline_invitation_on_page(_params(), page)

    assert result["status"] == "live_form_mismatch"
    assert "blocking" in result["message"].casefold()
    assert page.final_clicks == 0


@pytest.mark.asyncio
async def test_invitation_identity_is_rechecked_immediately_before_confirmation() -> None:
    page = _InvitationPage(_Dialog())
    page.before_final_identity_read = lambda: setattr(page, "title", "Changed role")

    result = await invitations._decline_invitation_on_page(_params(), page)

    assert result["status"] == "live_identity_mismatch"
    assert result["external_action_taken"] is False
    assert page.final_clicks == 0


@pytest.mark.asyncio
async def test_invitation_status_uses_only_one_exact_visible_scoped_control() -> None:
    page = _InvitationPage(_Dialog())
    page.body = "You have declined this invitation"

    identity = await invitations._current_invitation_identity(page, _params().invitation_url)

    assert identity and identity["invitation_status"] == "pending"

    page.status_elements = [_Element("declined", visible=False), _Element("pending")]
    identity = await invitations._current_invitation_identity(page, _params().invitation_url)
    assert identity and identity["invitation_status"] == "pending"

    page.body = "Pending invitation"
    page.status_elements = [_Element("declined")]
    identity = await invitations._current_invitation_identity(page, _params().invitation_url)
    assert identity and identity["invitation_status"] == "declined"

    page.status_elements = [_Element("pending"), _Element("declined")]
    assert await invitations._current_invitation_identity(
        page,
        _params().invitation_url,
    ) is None


@pytest.mark.asyncio
async def test_spoofed_decline_copy_cannot_confirm_pending_scoped_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(invitations.asyncio, "sleep", no_sleep)
    page = _InvitationPage(
        _Dialog(),
        update_scoped_status_on_confirm=False,
    )

    result = await invitations._decline_invitation_on_page(_params(), page)

    assert result["status"] == "unknown"
    assert result["owner_system_readback"]["confirmed"] is False
    assert result["owner_system_readback"]["invitation_identity"][
        "invitation_status"
    ] == "pending"
    assert page.body == "You have declined this invitation"
    assert page.final_clicks == 1


@pytest.mark.asyncio
async def test_final_decline_hidden_at_click_is_never_clicked() -> None:
    hidden_at_click = _HidesBeforeClickButton("Decline invitation")
    page = _InvitationPage(_Dialog(buttons=[hidden_at_click]))

    result = await invitations._decline_invitation_on_page(_params(), page)

    assert result["status"] == "live_form_mismatch"
    assert result["external_action_taken"] is False
    assert page.final_clicks == 0
    assert hidden_at_click.visibility_reads == 2


@pytest.mark.asyncio
async def test_failed_dialog_commit_consumes_one_shot_approval(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("UPWORK_MCP_STATE_DIR", str(tmp_path))
    page = _InvitationPage(_Dialog(buttons=[_Button("Confirm")]))
    monkeypatch.setattr(invitations, "get_browser", lambda: _Browser(page))
    params = _params()
    payload = invitations.invitation_decline_payload(params)
    prepared = prepare_action("invitation_decline", payload)
    approve_action(
        prepared["action_id"],
        prepared["approval_sha256"],
        owner_approval_reference="fresh exact approval",
    )
    params = params.model_copy(update={"action_id": prepared["action_id"]})

    result = await invitations.decline_invitation(params)
    replay = await invitations.decline_invitation(params)

    assert result["status"] == "live_form_mismatch"
    assert result["external_action_taken"] is False
    assert replay["status"] == "approval_required"
    assert "already been claimed" in replay["message"]
    assert page.final_clicks == 0
