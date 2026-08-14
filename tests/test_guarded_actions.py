"""Offline tests for approval-gated Upwork actions."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest
from pydantic import ValidationError

from upwork_mcp.prepared_actions import approve_action, prepare_action
from upwork_mcp.tools import invitations, messages, proposals


def _browser_must_not_open() -> None:
    raise AssertionError("browser access happened before approval")


def _approved(model, payload: dict[str, Any]):
    return model.model_copy(
        update={
            "approved": True,
            "approval_sha256": proposals.approval_payload_digest(payload),
        }
    )


def test_action_schemas_are_strict() -> None:
    with pytest.raises(ValidationError):
        proposals.SubmitProposalParams(
            job_url="https://www.upwork.com/jobs/~123",
            cover_letter="Exact copy",
            rate=63,
            unexpected=True,
        )
    with pytest.raises(ValidationError):
        proposals.SubmitProposalParams(
            job_url="https://evil.example/jobs/~123",
            cover_letter="Exact copy",
            rate=63,
        )
    with pytest.raises(ValidationError):
        proposals.SubmitProposalParams(
            job_url="https://www.upwork.com/jobs/~123",
            cover_letter="Exact copy",
            rate=63,
            bid=500,
        )
    with pytest.raises(ValidationError):
        messages.SendMessageParams(room_id="../../other", message="Exact copy")
    with pytest.raises(ValidationError):
        invitations.DeclineInvitationParams(
            invitation_url="https://www.upwork.com/ab/proposals/job/~123/apply/",
            invitation_id="3333333333333333333",
            job_title="Wrong route",
            invitation_status="pending",
            reason="   ",
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://www.upwork.com/nx/proposals/",
        "https://www.upwork.com/nx/proposals/archived",
        "https://www.upwork.com/nx/proposals/1111111111111111111/edit",
        "https://www.upwork.com/nx/proposals/interview/uid/3333333333333333333",
        "https://www.upwork.com/jobs/~abc?next=/nx/proposals/1111111111111111111",
    ],
)
def test_withdrawal_rejects_everything_except_individual_submitted_proposal(url: str) -> None:
    with pytest.raises(ValidationError):
        proposals.WithdrawProposalParams(
            proposal_url=url,
            proposal_id="1111111111111111111",
            job_title="Google Ads review",
            proposal_status="active",
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://www.upwork.com/nx/proposals/interview/uid/3333333333333333333/accept",
        "https://www.upwork.com/nx/proposals/job/~123/apply/",
        "https://www.upwork.com/jobs/~123",
        "https://www.upwork.com/nx/proposals/?next=/nx/proposals/interview/uid/3333333333333333333",
    ],
)
def test_decline_rejects_non_invitation_and_invitation_accept_routes(url: str) -> None:
    with pytest.raises(ValidationError):
        invitations.DeclineInvitationParams(
            invitation_url=url,
            invitation_id="3333333333333333333",
            job_title="Agency Google Ads support",
            invitation_status="pending",
        )


@pytest.mark.parametrize(
    ("url", "room_id"),
    [
        ("https://www.upwork.com/nx/messages/abc123456789", "abc123456789"),
        ("https://www.upwork.com/ab/messages/rooms/abc123456789", "abc123456789"),
    ],
)
def test_message_accepts_only_observed_individual_room_forms(url: str, room_id: str) -> None:
    params = messages.SendMessageParams(
        room_url=url,
        room_id=room_id,
        contact_name="Alex Client",
        message="Exact copy",
    )
    assert params.room_id == room_id


@pytest.mark.parametrize(
    "url",
    [
        "https://www.upwork.com/nx/messages",
        "https://www.upwork.com/ab/messages/rooms/",
        "https://www.upwork.com/jobs/~123?next=/nx/messages/abc123456789",
        "https://www.upwork.com/nx/messages/abc123456789/settings",
    ],
)
def test_message_rejects_indexes_subroutes_and_query_bypass(url: str) -> None:
    with pytest.raises(ValidationError):
        messages.SendMessageParams(
            room_url=url,
            room_id="abc123456789",
            contact_name="Alex Client",
            message="Exact copy",
        )


def test_all_action_ids_must_match_their_exact_routes() -> None:
    with pytest.raises(ValidationError):
        proposals.WithdrawProposalParams(
            proposal_url="https://www.upwork.com/nx/proposals/1111111111111111111",
            proposal_id="2222222222222222222",
            job_title="Google Ads review",
            proposal_status="active",
        )
    with pytest.raises(ValidationError):
        invitations.DeclineInvitationParams(
            invitation_url="https://www.upwork.com/nx/proposals/interview/uid/3333333333333333333",
            invitation_id="4444444444444444444",
            job_title="Agency Google Ads support",
            invitation_status="pending",
        )
    with pytest.raises(ValidationError):
        messages.SendMessageParams(
            room_url="https://www.upwork.com/nx/messages/abc123456789",
            room_id="different",
            contact_name="Alex Client",
            message="Exact copy",
        )


def test_submit_proposal_requires_exact_approval_before_browser(monkeypatch) -> None:
    monkeypatch.setattr(proposals, "get_browser", _browser_must_not_open)
    with pytest.raises(ValidationError):
        proposals.SubmitProposalParams(
            job_url="https://www.upwork.com/jobs/~123",
            job_id="~123",
            form_url="https://www.upwork.com/nx/proposals/job/~123/apply",
            job_title="Google Ads audit",
            job_type="hourly",
            cover_letter="Exact approved copy",
            rate=63,
            duration="1 to 3 months",
            base_connects=12,
        )


@pytest.mark.asyncio
async def test_approved_proposal_still_requires_live_preflight_before_browser(monkeypatch) -> None:
    monkeypatch.setattr(proposals, "get_browser", _browser_must_not_open)
    params = proposals.SubmitProposalParams(
        job_url="https://www.upwork.com/jobs/~123",
        job_id="~123",
        form_url="https://www.upwork.com/nx/proposals/job/~123/apply",
        job_title="Google Ads audit",
        job_type="hourly",
        action_id="uwa_missing_action",
        cover_letter="Exact approved copy",
        fee_net_text=["Upwork service fee $6.30", "You'll receive $56.70 net"],
        fee_net_status="complete",
        boost_auction_text=[],
        boost_auction_status="unavailable",
        rate=63,
        screening_questions_status="complete",
        duration="1 to 3 months",
        duration_options_status="complete",
        available_profile_highlights_status="complete",
        rate_increase_control_status="complete",
    )
    result = await proposals.submit_proposal(params)
    assert result["status"] == "approval_required"
    assert result["external_action_taken"] is False


@pytest.mark.asyncio
async def test_one_time_prepared_action_rejects_changed_terms_before_browser(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("UPWORK_MCP_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(proposals, "get_browser", _browser_must_not_open)
    original = proposals.SubmitProposalParams(
        job_url="https://www.upwork.com/jobs/~123",
        job_id="~123",
        form_url="https://www.upwork.com/nx/proposals/job/~123/apply",
        job_title="Google Ads audit",
        job_type="hourly",
        action_id="uwa_placeholder",
        cover_letter="Exact approved copy",
        fee_net_text=["Upwork service fee $6.30", "You'll receive $56.70 net"],
        fee_net_status="complete",
        boost_auction_text=[],
        boost_auction_status="unavailable",
        rate=63,
        screening_questions_status="complete",
        duration="1 to 3 months",
        duration_options_status="complete",
        available_profile_highlights_status="complete",
        rate_increase_control_status="complete",
    )
    payload = proposals.proposal_submission_payload(original)
    prepared = prepare_action("proposal", payload)
    approve_action(
        prepared["action_id"],
        prepared["approval_sha256"],
        owner_approval_reference="fresh exact approval",
    )

    changed = original.model_copy(update={"action_id": prepared["action_id"], "rate": 64})
    result = await proposals.submit_proposal(changed)
    assert result["status"] == "approval_required"
    assert "changed after approval" in result["message"]
    assert result["external_action_taken"] is False


@pytest.mark.asyncio
async def test_legacy_withdrawal_call_requires_identity_preflight(monkeypatch) -> None:
    monkeypatch.setattr(proposals, "get_browser", _browser_must_not_open)
    result = await proposals.withdraw_proposal("https://www.upwork.com/nx/proposals/1111111111111111111")
    assert result["status"] == "preflight_required"
    assert result["proposal_url"] == "https://www.upwork.com/nx/proposals/1111111111111111111"
    assert result["proposal_id"] == "1111111111111111111"
    assert result["external_action_taken"] is False


@pytest.mark.asyncio
async def test_message_requires_exact_approval_before_browser(monkeypatch) -> None:
    monkeypatch.setattr(messages, "get_browser", _browser_must_not_open)
    params = messages.SendMessageParams(
        room_url="https://www.upwork.com/nx/messages/room-1234567",
        room_id="room-1234567",
        contact_name="Alex Client",
        message="Exact approved message",
    )

    prepared = await messages.send_message(params)
    assert prepared["status"] == "approval_required"
    assert prepared["approval_sha256"] == proposals.approval_payload_digest(
        messages.message_payload(params)
    )

    mismatch = await messages.send_message(
        params.model_copy(update={"approved": True, "approval_sha256": "0" * 64})
    )
    assert mismatch["status"] == "approval_mismatch"


@pytest.mark.asyncio
async def test_decline_requires_exact_approval_before_browser(monkeypatch) -> None:
    monkeypatch.setattr(invitations, "get_browser", _browser_must_not_open)
    params = invitations.DeclineInvitationParams(
        invitation_url="https://www.upwork.com/nx/proposals/interview/uid/3333333333333333333",
        invitation_id="3333333333333333333",
        job_title="Agency Google Ads support",
        invitation_status="pending",
        reason="Not interested in work described",
        note="We work with agencies on a consultancy basis, but not as a full-time embedded team member.",
    )

    prepared = await invitations.decline_invitation(params)
    assert prepared["status"] == "approval_required"
    assert prepared["exact_payload"] == invitations.invitation_decline_payload(params)

    mismatch = await invitations.decline_invitation(
        params.model_copy(update={"approved": True, "approval_sha256": "0" * 64})
    )
    assert mismatch["status"] == "approval_mismatch"


class _TextElement:
    def __init__(self, text: str = "") -> None:
        self.text = text

    async def text_content(self) -> str:
        return self.text

    async def query_selector(self, _selector: str):
        return None

    async def query_selector_all(self, _selector: str) -> list[Any]:
        return []


class _Button(_TextElement):
    def __init__(self, callback: Callable[[], None], text: str = "") -> None:
        super().__init__(text)
        self.callback = callback

    async def click(self) -> None:
        self.callback()


class _HighlightOption(_TextElement):
    def __init__(self, title: str | None) -> None:
        super().__init__("Select highlight")
        self.title = title

    async def evaluate(self, _script: str) -> str | None:
        return self.title

    async def is_visible(self) -> bool:
        return True


class _HighlightTab(_Button):
    def __init__(self, tab_id: str, callback: Callable[[], None]) -> None:
        super().__init__(callback, tab_id.replace("_", " ").title())
        self.tab_id = tab_id

    async def get_attribute(self, name: str) -> str | None:
        return self.tab_id if name == "data-ev-tab" else None

    async def is_visible(self) -> bool:
        return True


class _Keyboard:
    def __init__(self, callback: Callable[[], None]) -> None:
        self.callback = callback

    async def press(self, key: str) -> None:
        if key == "Escape":
            self.callback()


class _Input(_TextElement):
    def __init__(self) -> None:
        super().__init__()
        self.value = ""

    async def fill(self, value: str) -> None:
        self.value = value

    async def press(self, _key: str) -> None:
        self.value = ""

    async def input_value(self) -> str:
        return self.value


class _MessageElement(_TextElement):
    def __init__(self, content: str, *, is_mine: bool) -> None:
        super().__init__(content)
        self.content = content
        self.is_mine = is_mine

    async def query_selector(self, selector: str):
        if any(part in selector for part in ('[data-test="content"]', ".content", ".message-text", "p")):
            return _TextElement(self.content)
        if self.is_mine and any(part in selector for part in (".my-message", '[data-test="my-message"]', ".sent")):
            return _TextElement()
        return None


class _MessagePage:
    def __init__(self, *, contact_name: str = "Alex Client") -> None:
        self.url = "https://www.upwork.com/nx/messages/room-1234567"
        self.contact_name = contact_name
        self.action_controls_queried = 0
        self.messages: list[_MessageElement] = []
        self.input = _Input()

    async def goto(self, url: str, **_kwargs) -> None:
        self.url = url

    async def query_selector_all(self, selector: str) -> list[Any]:
        if "message" in selector:
            return self.messages
        return []

    async def query_selector(self, selector: str):
        if "contact-name" in selector or ".contact-name" in selector or "h2" in selector:
            return _TextElement(self.contact_name)
        if "message-input" in selector or "textarea" in selector:
            self.action_controls_queried += 1
            return self.input
        if "send-button" in selector or "Send" in selector:
            self.action_controls_queried += 1
            def send() -> None:
                self.messages.append(_MessageElement(self.input.value, is_mine=True))
                self.input.value = ""

            return _Button(send)
        return None


class _ProposalPage:
    def __init__(self, *, title: str, status: str = "active") -> None:
        self.url = "https://www.upwork.com/nx/proposals/1111111111111111111"
        self.title = title
        self.status = status
        self.body = "Submitted proposal details"
        self.action_controls_queried = 0

    async def goto(self, url: str, **_kwargs) -> None:
        self.url = url

    async def query_selector(self, selector: str):
        if selector == "body":
            return _TextElement(self.body)
        if "job-title" in selector or "h1" in selector:
            return _TextElement(self.title)
        if "proposal-status" in selector or selector == ".status":
            return _TextElement(self.status)
        if "withdraw-button" in selector or "Withdraw" in selector:
            self.action_controls_queried += 1
            return _Button(lambda: None, "Withdraw")
        return None

    async def query_selector_all(self, _selector: str) -> list[Any]:
        return []


class _InvitationPage:
    def __init__(self, *, title: str) -> None:
        self.url = "https://www.upwork.com/nx/proposals/interview/uid/3333333333333333333"
        self.title = title
        self.body = "Pending invitation"
        self.action_controls_queried = 0

    async def goto(self, url: str, **_kwargs) -> None:
        self.url = url

    async def query_selector(self, selector: str):
        if selector == "body":
            return _TextElement(self.body)
        if "job-title" in selector or "h1" in selector:
            return _TextElement(self.title)
        if "decline-button" in selector or "Decline" in selector:
            self.action_controls_queried += 1
            return _Button(lambda: None, "Decline invitation")
        return None

    async def query_selector_all(self, _selector: str) -> list[Any]:
        return []


class _Browser:
    def __init__(self, page: Any) -> None:
        self.page = page

    async def ensure_logged_in(self) -> None:
        return None

    async def get_page(self):
        return self.page

    @asynccontextmanager
    async def operation(self):
        yield self.page


@pytest.mark.asyncio
async def test_approved_message_requires_exact_owner_system_readback(monkeypatch) -> None:
    page = _MessagePage()
    monkeypatch.setattr(messages, "get_browser", lambda: _Browser(page))
    params = messages.SendMessageParams(
        room_url="https://www.upwork.com/nx/messages/room-1234567",
        room_id="room-1234567",
        contact_name="Alex Client",
        message="Exact approved message",
    )
    params = _approved(params, messages.message_payload(params))

    result = await messages.send_message(params)
    assert result["status"] == "sent"
    assert result["external_action_taken"] is True
    assert result["owner_system_readback"]["confirmed"] is True
    assert result["owner_system_readback"]["exact_visible_copy"] is True
    assert result["owner_system_readback"]["conversation_identity"]["contact_name"] == "Alex Client"


@pytest.mark.asyncio
async def test_approved_message_stops_if_live_recipient_changed(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("UPWORK_MCP_STATE_DIR", str(tmp_path))
    page = _MessagePage(contact_name="Different Client")
    monkeypatch.setattr(messages, "get_browser", lambda: _Browser(page))
    params = messages.SendMessageParams(
        room_url="https://www.upwork.com/nx/messages/room-1234567",
        room_id="room-1234567",
        contact_name="Alex Client",
        message="Exact approved message",
    )
    payload = messages.message_payload(params)
    prepared = prepare_action("message", payload)
    approve_action(
        prepared["action_id"],
        prepared["approval_sha256"],
        owner_approval_reference="fresh exact approval",
    )
    params = params.model_copy(update={"action_id": prepared["action_id"]})

    result = await messages.send_message(params)
    assert result["status"] == "live_identity_mismatch"
    assert result["external_action_taken"] is False
    assert page.messages == []
    assert page.action_controls_queried == 0
    replay = await messages.send_message(params)
    assert replay["status"] == "approval_required"
    assert "already been claimed" in replay["message"]


@pytest.mark.asyncio
async def test_approved_withdrawal_stops_if_live_proposal_identity_changed(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("UPWORK_MCP_STATE_DIR", str(tmp_path))
    page = _ProposalPage(title="Different Google Ads job")
    monkeypatch.setattr(proposals, "get_browser", lambda: _Browser(page))
    params = proposals.WithdrawProposalParams(
        proposal_url="https://www.upwork.com/nx/proposals/1111111111111111111",
        proposal_id="1111111111111111111",
        job_title="Approved Google Ads job",
        proposal_status="active",
    )
    payload = proposals.proposal_withdrawal_payload(params)
    prepared = prepare_action("withdrawal", payload)
    approve_action(
        prepared["action_id"],
        prepared["approval_sha256"],
        owner_approval_reference="fresh exact approval",
    )
    params = params.model_copy(update={"action_id": prepared["action_id"]})

    result = await proposals.withdraw_proposal(params)
    assert result["status"] == "live_identity_mismatch"
    assert result["external_action_taken"] is False
    assert page.action_controls_queried == 0
    replay = await proposals.withdraw_proposal(params)
    assert replay["status"] == "approval_required"
    assert "already been claimed" in replay["message"]


@pytest.mark.asyncio
async def test_approved_decline_stops_if_live_invitation_identity_changed(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("UPWORK_MCP_STATE_DIR", str(tmp_path))
    page = _InvitationPage(title="Different agency role")
    monkeypatch.setattr(invitations, "get_browser", lambda: _Browser(page))
    params = invitations.DeclineInvitationParams(
        invitation_url="https://www.upwork.com/nx/proposals/interview/uid/3333333333333333333",
        invitation_id="3333333333333333333",
        job_title="Approved agency role",
        invitation_status="pending",
    )
    payload = invitations.invitation_decline_payload(params)
    prepared = prepare_action("invitation_decline", payload)
    approve_action(
        prepared["action_id"],
        prepared["approval_sha256"],
        owner_approval_reference="fresh exact approval",
    )
    params = params.model_copy(update={"action_id": prepared["action_id"]})

    result = await invitations.decline_invitation(params)
    assert result["status"] == "live_identity_mismatch"
    assert result["external_action_taken"] is False
    assert page.action_controls_queried == 0
    replay = await invitations.decline_invitation(params)
    assert replay["status"] == "approval_required"
    assert "already been claimed" in replay["message"]


class _PaymentTerms(_TextElement):
    def __init__(self) -> None:
        super().__init__("By milestone By project")

    async def query_selector_all(self, selector: str) -> list[Any]:
        if 'type="radio"' in selector and (
            "project" in selector.casefold() or "milestone" in selector.casefold()
        ):
            return [_TextElement()]
        return []


class _InspectPage:
    def __init__(
        self,
        *,
        unresolved_highlight: bool = False,
        dismiss_highlight: bool = True,
        escape_dismisses_highlight: bool = False,
        missing_highlight_tab: str | None = None,
        screening_answer_count: int = 1,
        duration_options: list[str] | None = None,
        duration_control: bool = True,
    ) -> None:
        self.url = "https://www.upwork.com/jobs/~123"
        self.form_open = False
        self.highlight_open = False
        self.duration_open = False
        self.highlight_tab = "portfolio"
        self.dismiss_highlight = dismiss_highlight
        self.escape_dismisses_highlight = escape_dismisses_highlight
        self.duration_control = duration_control
        self.duration_options = duration_options or list(proposals._DURATION_OPTIONS)
        self.keyboard = _Keyboard(self._escape)
        self.body = "Google Ads audit job"
        self.highlight_options: dict[str, list[str | None]] = {
            "portfolio": ["  Family   Law Growth  ", "Home Services Lead Generation"],
            "certifications": ["Google Ads Search Certification"],
            "upwork_jobs": [None if unresolved_highlight else "Google Ads Account Audit"],
        }
        if missing_highlight_tab:
            self.highlight_options.pop(missing_highlight_tab)
        self.payment_terms = _PaymentTerms()
        self.cover_control = _TextElement()
        self.answer_controls = [_TextElement() for _ in range(screening_answer_count)]

    def _escape(self) -> None:
        self.duration_open = False
        if self.escape_dismisses_highlight:
            self.highlight_open = False

    async def goto(self, url: str, **_kwargs) -> None:
        self.url = url

    async def wait_for_load_state(self, _state: str) -> None:
        return None

    async def wait_for_timeout(self, _milliseconds: int) -> None:
        return None

    async def query_selector(self, selector: str):
        if selector == "body":
            return _TextElement(self.body)
        if '[data-test="job-title"]' in selector:
            return _TextElement("Google Ads audit")
        if "cover-letter-input" in selector or "textarea" in selector:
            return _TextElement() if self.form_open else None
        if "apply-button" in selector:
            def open_form() -> None:
                self.form_open = True
                self.url = "https://www.upwork.com/ab/proposals/job/~123/apply/"
                self.body = """Google Ads audit
Fixed-price project
By milestone
By project
12 Connects required to submit
Upwork service fee $50
You'll receive $450 net
Less than 1 month
1 to 3 months
3 to 6 months
More than 6 months
Boost your proposal auction: top bid 8 Connects
"""

            return _Button(open_form, "Apply Now")
        if self.form_open and "Add a portfolio project" in selector:
            return _Button(lambda: setattr(self, "highlight_open", True), "Add a portfolio project")
        if self.highlight_open and "aria-label" in selector and "Close" in selector:
            def close_highlights() -> None:
                if self.dismiss_highlight:
                    self.highlight_open = False

            return _Button(close_highlights, "Close")
        if self.highlight_open and "Add profile highlights" in selector:
            return _TextElement("Add profile highlights")
        return None

    async def query_selector_all(self, selector: str) -> list[Any]:
        if self.form_open and "payment-terms" in selector:
            return [self.payment_terms]
        if self.form_open and selector == proposals._SCREENING_QUESTION_PROMPTS:
            return [_TextElement("What similar work have you done?")]
        if self.form_open and selector == proposals._SCREENING_ANSWER_CONTROLS:
            return self.answer_controls
        if self.form_open and selector == proposals._COVER_LETTER_CONTROL:
            return [self.cover_control]
        if self.form_open and selector == "textarea":
            return [self.cover_control, *self.answer_controls]
        if self.form_open and selector == proposals._DURATION_TOGGLE:
            return [] if not self.duration_control else [
                _Button(lambda: setattr(self, "duration_open", True), "Select a duration")
            ]
        if self.duration_open and selector == proposals._DURATION_MENU_OPTIONS:
            return [_TextElement(option) for option in self.duration_options]
        if self.highlight_open and 'role="tab"' in selector:
            return [
                _HighlightTab(tab_id, lambda tab_id=tab_id: setattr(self, "highlight_tab", tab_id))
                for tab_id in self.highlight_options
            ]
        if self.highlight_open and "Select highlight" in selector:
            return [_HighlightOption(title) for title in self.highlight_options[self.highlight_tab]]
        return []


@pytest.mark.asyncio
async def test_inspect_proposal_form_is_read_only_and_returns_live_fields(monkeypatch) -> None:
    page = _InspectPage()
    monkeypatch.setattr(proposals, "get_browser", lambda: _Browser(page))

    result = await proposals.inspect_proposal_form("https://www.upwork.com/jobs/~123")
    assert result["form_status"] == "ready"
    assert result["job_id"] == "~123"
    assert result["form_url"] == "https://www.upwork.com/nx/proposals/job/~123/apply"
    assert result["job_title"] == "Google Ads audit"
    assert result["job_type"] == "fixed"
    assert result["fixed_payment_structures"] == ["by_project", "by_milestone"]
    assert result["base_connects"] == 12
    assert result["screening_questions"] == ["What similar work have you done?"]
    assert result["screening_questions_status"] == "complete"
    assert result["duration_options"] == [
        "Less than 1 month",
        "1 to 3 months",
        "3 to 6 months",
        "More than 6 months",
    ]
    assert result["duration_options_status"] == "complete"
    assert result["fee_net_text"]
    assert result["fee_net_status"] == "complete"
    assert result["boost_auction_text"]
    assert result["boost_auction_status"] == "complete"
    assert result["available_profile_highlights"] == [
        "Family Law Growth",
        "Home Services Lead Generation",
        "Google Ads Search Certification",
        "Google Ads Account Audit",
    ]
    assert result["available_profile_highlights_status"] == "complete"
    assert result["rate_increase_control_status"] == "not_applicable"
    assert result["available_profile_highlights_details"]["chooser_dismissed"] is True
    assert result["available_profile_highlights_details"]["tabs_inspected"] == [
        "portfolio",
        "certifications",
        "upwork_jobs",
    ]
    assert page.highlight_open is False
    assert result["external_action_taken"] is False


@pytest.mark.asyncio
async def test_inspect_proposal_form_marks_unreadable_highlight_titles_incomplete(monkeypatch) -> None:
    page = _InspectPage(unresolved_highlight=True)
    monkeypatch.setattr(proposals, "get_browser", lambda: _Browser(page))

    result = await proposals.inspect_proposal_form("https://www.upwork.com/jobs/~123")

    assert result["available_profile_highlights_status"] == "incomplete"
    assert result["available_profile_highlights_details"]["selectable_options_seen"] == 4
    assert result["available_profile_highlights_details"]["titles_extracted"] == 3
    assert "1 selectable option" in result["available_profile_highlights_details"]["message"]
    assert page.highlight_open is False
    assert result["external_action_taken"] is False


@pytest.mark.asyncio
async def test_inspect_proposal_form_marks_undismissed_highlight_chooser_incomplete(monkeypatch) -> None:
    page = _InspectPage(dismiss_highlight=False)
    monkeypatch.setattr(proposals, "get_browser", lambda: _Browser(page))

    result = await proposals.inspect_proposal_form("https://www.upwork.com/jobs/~123")

    assert result["available_profile_highlights_status"] == "incomplete"
    assert result["available_profile_highlights_details"]["chooser_dismissed"] is False
    assert "could not be dismissed" in result["available_profile_highlights_details"]["message"]
    assert result["external_action_taken"] is False


@pytest.mark.asyncio
async def test_inspect_proposal_form_uses_escape_when_close_does_not_dismiss(monkeypatch) -> None:
    page = _InspectPage(dismiss_highlight=False, escape_dismisses_highlight=True)
    monkeypatch.setattr(proposals, "get_browser", lambda: _Browser(page))

    result = await proposals.inspect_proposal_form("https://www.upwork.com/jobs/~123")

    assert result["available_profile_highlights_status"] == "complete"
    assert result["available_profile_highlights_details"]["chooser_dismissed"] is True
    assert page.highlight_open is False
    assert result["external_action_taken"] is False


@pytest.mark.asyncio
async def test_inspect_proposal_form_fails_closed_when_known_highlight_tab_is_missing(
    monkeypatch,
) -> None:
    page = _InspectPage(missing_highlight_tab="upwork_jobs")
    monkeypatch.setattr(proposals, "get_browser", lambda: _Browser(page))

    result = await proposals.inspect_proposal_form("https://www.upwork.com/jobs/~123")

    assert result["available_profile_highlights_status"] == "incomplete"
    assert result["available_profile_highlights_details"]["missing_required_tabs"] == [
        "upwork_jobs"
    ]
    assert result["available_profile_highlights_details"]["missing_inspected_tabs"] == [
        "upwork_jobs"
    ]
    assert page.highlight_open is False


@pytest.mark.asyncio
async def test_inspect_proposal_form_reports_incomplete_screening_control_enumeration(
    monkeypatch,
) -> None:
    page = _InspectPage(screening_answer_count=0)
    monkeypatch.setattr(proposals, "get_browser", lambda: _Browser(page))

    result = await proposals.inspect_proposal_form("https://www.upwork.com/jobs/~123")

    assert result["screening_questions"] == ["What similar work have you done?"]
    assert result["screening_questions_status"] == "incomplete"
    assert result["screening_questions_details"]["answer_controls_seen"] == 0


@pytest.mark.asyncio
async def test_inspect_proposal_form_reports_duration_discovery_statuses(monkeypatch) -> None:
    missing_option = _InspectPage(duration_options=proposals._DURATION_OPTIONS[:-1])
    monkeypatch.setattr(proposals, "get_browser", lambda: _Browser(missing_option))
    incomplete = await proposals.inspect_proposal_form("https://www.upwork.com/jobs/~123")
    assert incomplete["duration_options_status"] == "incomplete"
    assert incomplete["duration_options_details"]["missing_options"] == ["More than 6 months"]

    no_control = _InspectPage(duration_control=False)
    monkeypatch.setattr(proposals, "get_browser", lambda: _Browser(no_control))
    unavailable = await proposals.inspect_proposal_form("https://www.upwork.com/jobs/~123")
    assert unavailable["duration_options_status"] == "unavailable"


class _RateSelect(_TextElement):
    def __init__(self, options: list[str]) -> None:
        super().__init__()
        self.options = options

    async def is_visible(self) -> bool:
        return True

    async def query_selector_all(self, selector: str) -> list[Any]:
        return [_ClosedNativeOption(option) for option in self.options] if selector == "option" else []


class _ClosedNativeOption(_TextElement):
    async def is_visible(self) -> bool:
        return False


class _RateInspectionPage:
    def __init__(self, options: list[str] | None) -> None:
        self.select = _RateSelect(options) if options is not None else None

    async def query_selector_all(self, selector: str) -> list[Any]:
        if selector == proposals._RATE_INCREASE_SELECT:
            return [self.select] if self.select else []
        return []


@pytest.mark.asyncio
async def test_rate_increase_control_status_is_explicit_and_fail_closed() -> None:
    complete = await proposals._inspect_rate_increase_control(
        _RateInspectionPage(["Never", "Every 3 months"]),
        "hourly",
    )
    assert complete["status"] == "complete"

    incomplete = await proposals._inspect_rate_increase_control(
        _RateInspectionPage(["Every 3 months"]),
        "hourly",
    )
    assert incomplete["status"] == "incomplete"

    not_applicable = await proposals._inspect_rate_increase_control(
        _RateInspectionPage(None),
        "hourly",
    )
    assert not_applicable["status"] == "not_applicable"


@pytest.mark.asyncio
async def test_live_fee_and_boost_context_helpers_normalize_and_classify_exact_state() -> None:
    fee = await proposals._inspect_fee_net_state(
        None,
        "  Upwork service fee   $6.30  \nYou'll receive $56.70 net\nUpwork service fee $6.30",
    )
    assert fee == {
        "text": ["Upwork service fee $6.30", "You'll receive $56.70 net"],
        "status": "complete",
        "details": {
            "fee_lines_seen": 1,
            "net_lines_seen": 1,
            "message": "Both the live Upwork fee and freelancer net preview were read.",
        },
    }

    generic_bid = await proposals._inspect_boost_auction_state(
        None,
        "Boost your proposal\nPlace a bid",
    )
    assert generic_bid["status"] == "incomplete"
    live_auction = await proposals._inspect_boost_auction_state(
        None,
        "Boost your proposal auction: top bid 8 Connects",
    )
    assert live_auction["status"] == "complete"
    no_bids = await proposals._inspect_boost_auction_state(
        None,
        "Boost your proposal\nNo bids yet",
    )
    assert no_bids["status"] == "complete"
