"""Offline tests for approval-gated Upwork actions."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from upwork_mcp.prepared_actions import approve_action, prepare_action
from upwork_mcp.tools import invitations, messages, proposals


def _browser_must_not_open() -> None:
    raise AssertionError("browser access happened before approval")


def _approved_once(model, payload: dict[str, Any], monkeypatch, state_dir):
    monkeypatch.setenv("UPWORK_MCP_STATE_DIR", str(state_dir))
    action_type = {
        "SendMessageParams": "message",
        "WithdrawProposalParams": "withdrawal",
        "DeclineInvitationParams": "invitation_decline",
    }[type(model).__name__]
    prepared = prepare_action(action_type, payload)
    approve_action(
        prepared["action_id"],
        prepared["approval_sha256"],
        owner_approval_reference="fresh exact approval",
    )
    return model.model_copy(update={"action_id": prepared["action_id"]})


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
        "https://www.upwork.com/nx/messages/abc123456789?next=/nx/messages/different123",
        "https://www.upwork.com/ab/messages/rooms/abc123456789?room=other123456",
        "https://www.upwork.com/nx/messages/abc123456789#different-room",
        "https://www.upwork.com/nx/messages/abc123456789?",
        "https://www.upwork.com/nx/messages/abc123456789#",
        "https://www.upwork.com.evil.example/nx/messages/abc123456789",
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


@pytest.mark.parametrize(
    ("url", "canonical"),
    [
        (
            "https://www.upwork.com/nx/messages/abc123456789",
            "https://www.upwork.com/nx/messages/abc123456789",
        ),
        (
            "https://www.upwork.com/ab/messages/rooms/abc123456789",
            "https://www.upwork.com/ab/messages/rooms/abc123456789",
        ),
    ],
)
def test_message_room_parser_returns_exact_room_not_route_container(
    url: str, canonical: str
) -> None:
    assert messages.parse_message_room(url) == (canonical, "abc123456789")


def test_message_room_parser_does_not_mistake_http_prefixed_id_for_url() -> None:
    assert messages.parse_message_room("http12345678") == (
        "https://www.upwork.com/nx/messages/http12345678",
        "http12345678",
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
        fee_net_price_amount="63.00",
        fee_net_source="scoped_reversible_price_preflight",
        boost_auction_text=[],
        boost_auction_status="unavailable",
        rate=63,
        screening_questions_status="complete",
        duration="1 to 3 months",
        duration_options_status="complete",
        available_profile_highlights_status="complete",
        base_connects=12,
        base_connects_status="complete",
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
        fee_net_price_amount="63.00",
        fee_net_source="scoped_reversible_price_preflight",
        boost_auction_text=[],
        boost_auction_status="unavailable",
        rate=63,
        screening_questions_status="complete",
        duration="1 to 3 months",
        duration_options_status="complete",
        available_profile_highlights_status="complete",
        base_connects=12,
        base_connects_status="complete",
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
async def test_legacy_digest_cannot_authorize_withdrawal(monkeypatch) -> None:
    monkeypatch.setattr(proposals, "get_browser", _browser_must_not_open)
    params = _withdrawal_params()
    payload = proposals.proposal_withdrawal_payload(params)
    result = await proposals.withdraw_proposal(
        params.model_copy(
            update={
                "approved": True,
                "approval_sha256": proposals.approval_payload_digest(payload),
            }
        )
    )

    assert result["status"] == "approval_required"
    assert result["legacy_authorization_rejected"] is True
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
    assert mismatch["status"] == "approval_required"
    assert mismatch["legacy_authorization_rejected"] is True


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
    assert mismatch["status"] == "approval_required"
    assert mismatch["legacy_authorization_rejected"] is True


class _TextElement:
    def __init__(self, text: str = "") -> None:
        self.text = text

    async def text_content(self) -> str:
        return self.text

    async def is_visible(self) -> bool:
        return True

    async def is_enabled(self) -> bool:
        return True

    async def get_attribute(self, _name: str) -> str | None:
        return None

    async def query_selector(self, _selector: str):
        return None

    async def query_selector_all(self, _selector: str) -> list[Any]:
        return []


class _Button(_TextElement):
    def __init__(
        self,
        callback: Callable[[], None],
        text: str = "",
        *,
        attributes: dict[str, str] | None = None,
    ) -> None:
        super().__init__(text)
        self.callback = callback
        self.attributes = attributes or {}

    async def click(self) -> None:
        self.callback()

    async def get_attribute(self, name: str) -> str | None:
        return self.attributes.get(name)


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
        self.corrupt_readback = False
        self.fail_restore = False
        self.press_count = 0

    async def fill(self, value: str) -> None:
        if not value and self.value and self.fail_restore:
            raise RuntimeError("restore failed")
        self.value = value

    async def press(self, _key: str) -> None:
        self.press_count += 1
        self.value = ""

    async def input_value(self) -> str:
        return f"{self.value} " if self.corrupt_readback and self.value else self.value


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

    async def query_selector_all(self, selector: str) -> list[Any]:
        if selector == '[data-test="content"]':
            return [_TextElement(self.content)]
        return []


class _Link(_TextElement):
    def __init__(self, href: str) -> None:
        super().__init__()
        self.href = href

    async def get_attribute(self, name: str) -> str | None:
        return self.href if name == "href" else None


class _ConversationListElement(_TextElement):
    def __init__(self, href: str) -> None:
        super().__init__()
        self.href = href

    async def query_selector(self, selector: str):
        if "contact-name" in selector:
            return _TextElement("Alex Client")
        if selector == 'a[href*="/messages/"]':
            return _Link(self.href)
        return None


class _MessageComposer(_TextElement):
    def __init__(self, page: _MessagePage) -> None:
        super().__init__()
        self.page = page

        def send() -> None:
            self.page.send_clicks += 1
            self.page.messages.append(_MessageElement(self.page.input.value, is_mine=True))
            self.page.input.value = ""

        self.send_button = _Button(
            send,
            "Send",
            attributes={"data-test": "send-button", "type": "submit"},
        )

    async def query_selector_all(self, selector: str) -> list[Any]:
        if selector == messages._COMPOSER_INPUT_SELECTOR:
            self.page.action_controls_queried += 1
            return [self.page.input]
        if selector == messages._SCOPED_SEND_CANDIDATE_SELECTOR:
            self.page.action_controls_queried += 1
            if self.page.on_send_resolution is not None:
                callback = self.page.on_send_resolution
                self.page.on_send_resolution = None
                callback()
            return [self.send_button] if self.page.scoped_send_available else []
        return []


class _MessagePage:
    def __init__(
        self,
        *,
        contact_name: str = "Alex Client",
        composer_count: int = 1,
        scoped_send_available: bool = True,
    ) -> None:
        self.url = "https://www.upwork.com/nx/messages/room-1234567"
        self.contact_name = contact_name
        self.action_controls_queried = 0
        self.page_wide_send_queries = 0
        self.send_clicks = 0
        self.messages: list[_MessageElement] = []
        self.input = _Input()
        self.scoped_send_available = scoped_send_available
        self.on_send_resolution: Callable[[], None] | None = None
        self.composers = [_MessageComposer(self) for _ in range(composer_count)]

    async def goto(self, url: str, **_kwargs) -> None:
        self.url = url

    async def query_selector_all(self, selector: str) -> list[Any]:
        if selector == messages._ROOM_CONTACT_SELECTOR:
            return [_TextElement(self.contact_name)]
        if selector == messages._MESSAGE_RECORD_SELECTOR:
            return self.messages
        if selector == messages._COMPOSER_SELECTOR:
            self.action_controls_queried += 1
            return self.composers
        return []

    async def query_selector(self, selector: str):
        if "contact-name" in selector or ".contact-name" in selector or "h2" in selector:
            return _TextElement(self.contact_name)
        if "message-input" in selector or "textarea" in selector:
            self.action_controls_queried += 1
            return self.input
        if "send-button" in selector or "Send" in selector:
            self.page_wide_send_queries += 1
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
@pytest.mark.parametrize(
    ("href", "canonical"),
    [
        (
            "/nx/messages/abc123456789",
            "https://www.upwork.com/nx/messages/abc123456789",
        ),
        (
            "/ab/messages/rooms/abc123456789",
            "https://www.upwork.com/ab/messages/rooms/abc123456789",
        ),
    ],
)
async def test_conversation_list_extraction_uses_exact_room_parser(
    href: str, canonical: str
) -> None:
    result = await messages._extract_conversation(_ConversationListElement(href))
    assert result is not None
    assert result["room_url"] == canonical
    assert result["room_id"] == "abc123456789"
    assert result["room_id"] != "rooms"


@pytest.mark.asyncio
async def test_conversation_list_extraction_rejects_query_room_bypass() -> None:
    result = await messages._extract_conversation(
        _ConversationListElement(
            "/ab/messages/rooms/abc123456789?next=/nx/messages/different123"
        )
    )
    assert result is None


@pytest.mark.asyncio
async def test_approved_message_requires_exact_owner_system_readback(monkeypatch, tmp_path) -> None:
    page = _MessagePage()
    monkeypatch.setattr(messages, "get_browser", lambda: _Browser(page))
    params = messages.SendMessageParams(
        room_url="https://www.upwork.com/nx/messages/room-1234567",
        room_id="room-1234567",
        contact_name="Alex Client",
        message="Exact approved message",
    )
    params = _approved_once(params, messages.message_payload(params), monkeypatch, tmp_path)

    result = await messages.send_message(params)
    assert result["status"] == "sent"
    assert result["external_action_taken"] is True
    assert result["owner_system_readback"]["confirmed"] is True
    assert result["owner_system_readback"]["exact_visible_copy"] is True
    assert result["owner_system_readback"]["exact_composer_copy_before_send"] is True
    assert result["owner_system_readback"]["composer_cleared_after_send"] is True
    assert result["owner_system_readback"]["exact_copy_is_last_visible_message"] is True
    assert result["owner_system_readback"]["visible_history_complete"] is True
    assert result["owner_system_readback"]["conversation_identity"]["contact_name"] == "Alex Client"
    assert page.page_wide_send_queries == 0
    assert page.input.press_count == 0


@pytest.mark.asyncio
async def test_approved_message_blocks_older_exact_copy_anywhere_in_visible_history(
    monkeypatch,
    tmp_path,
) -> None:
    page = _MessagePage()
    page.messages = [
        _MessageElement("Exact approved message", is_mine=True),
        _MessageElement("A newer different message", is_mine=True),
    ]
    monkeypatch.setattr(messages, "get_browser", lambda: _Browser(page))
    params = messages.SendMessageParams(
        room_url="https://www.upwork.com/nx/messages/room-1234567",
        room_id="room-1234567",
        contact_name="Alex Client",
        message="Exact approved message",
    )

    result = await messages.send_message(
        _approved_once(params, messages.message_payload(params), monkeypatch, tmp_path)
    )

    assert result["status"] == "duplicate_blocked"
    assert result["owner_system_readback"]["rendered_record_count"] == 2
    assert result["external_action_taken"] is False
    assert page.action_controls_queried == 0


@pytest.mark.asyncio
async def test_approved_message_does_not_treat_matching_inbound_copy_as_our_duplicate(
    monkeypatch,
    tmp_path,
) -> None:
    page = _MessagePage()
    page.messages = [_MessageElement("Thanks!", is_mine=False)]
    monkeypatch.setattr(messages, "get_browser", lambda: _Browser(page))
    params = messages.SendMessageParams(
        room_url="https://www.upwork.com/nx/messages/room-1234567",
        room_id="room-1234567",
        contact_name="Alex Client",
        message="Thanks!",
    )

    result = await messages.send_message(
        _approved_once(params, messages.message_payload(params), monkeypatch, tmp_path)
    )

    assert result["status"] == "sent"
    assert result["owner_system_readback"]["matching_messages_before"] == 0


@pytest.mark.asyncio
async def test_approved_message_requires_byte_exact_duplicate_before_blocking(
    monkeypatch, tmp_path
) -> None:
    page = _MessagePage()
    page.messages = [_MessageElement("Exact approved message ", is_mine=True)]
    monkeypatch.setattr(messages, "get_browser", lambda: _Browser(page))
    params = messages.SendMessageParams(
        room_url="https://www.upwork.com/nx/messages/room-1234567",
        room_id="room-1234567",
        contact_name="Alex Client",
        message="Exact approved message",
    )

    result = await messages.send_message(
        _approved_once(params, messages.message_payload(params), monkeypatch, tmp_path)
    )

    assert result["status"] == "sent"
    assert result["owner_system_readback"]["matching_messages_before"] == 0


class _UnreadableMessageElement(_MessageElement):
    async def query_selector_all(self, _selector: str) -> list[Any]:
        raise RuntimeError("detached")


@pytest.mark.asyncio
async def test_approved_message_fails_closed_when_visible_history_is_incomplete(
    monkeypatch,
    tmp_path,
) -> None:
    page = _MessagePage()
    page.messages = [_UnreadableMessageElement("Unreadable", is_mine=True)]
    monkeypatch.setattr(messages, "get_browser", lambda: _Browser(page))
    params = messages.SendMessageParams(
        room_url="https://www.upwork.com/nx/messages/room-1234567",
        room_id="room-1234567",
        contact_name="Alex Client",
        message="Exact approved message",
    )

    result = await messages.send_message(
        _approved_once(params, messages.message_payload(params), monkeypatch, tmp_path)
    )

    assert result["status"] == "history_unreadable"
    assert result["visible_history_readback"]["status"] == "incomplete"
    assert result["external_action_taken"] is False
    assert page.action_controls_queried == 0


@pytest.mark.asyncio
async def test_approved_message_never_uses_page_send_or_enter_fallback(
    monkeypatch, tmp_path
) -> None:
    page = _MessagePage(scoped_send_available=False)
    monkeypatch.setattr(messages, "get_browser", lambda: _Browser(page))
    params = messages.SendMessageParams(
        room_url="https://www.upwork.com/nx/messages/room-1234567",
        room_id="room-1234567",
        contact_name="Alex Client",
        message="Exact approved message",
    )

    result = await messages.send_message(
        _approved_once(params, messages.message_payload(params), monkeypatch, tmp_path)
    )

    assert result["status"] == "send_control_unavailable"
    assert result["external_action_taken"] is False
    assert page.page_wide_send_queries == 0
    assert page.input.press_count == 0
    assert page.input.value == ""
    assert result["composer_restored"] is True
    assert page.messages == []


@pytest.mark.asyncio
async def test_approved_message_requires_one_exact_visible_composer(monkeypatch, tmp_path) -> None:
    page = _MessagePage(composer_count=2)
    monkeypatch.setattr(messages, "get_browser", lambda: _Browser(page))
    params = messages.SendMessageParams(
        room_url="https://www.upwork.com/nx/messages/room-1234567",
        room_id="room-1234567",
        contact_name="Alex Client",
        message="Exact approved message",
    )

    result = await messages.send_message(
        _approved_once(params, messages.message_payload(params), monkeypatch, tmp_path)
    )

    assert result["status"] == "composer_unavailable"
    assert result["external_action_taken"] is False
    assert page.input.value == ""
    assert page.messages == []


@pytest.mark.asyncio
async def test_approved_message_requires_byte_exact_composer_readback(
    monkeypatch, tmp_path
) -> None:
    page = _MessagePage()
    page.input.corrupt_readback = True
    monkeypatch.setattr(messages, "get_browser", lambda: _Browser(page))
    params = messages.SendMessageParams(
        room_url="https://www.upwork.com/nx/messages/room-1234567",
        room_id="room-1234567",
        contact_name="Alex Client",
        message="Exact approved message",
    )

    result = await messages.send_message(
        _approved_once(params, messages.message_payload(params), monkeypatch, tmp_path)
    )

    assert result["status"] == "composer_readback_mismatch"
    assert result["external_action_taken"] is False
    assert result["composer_restored"] is True
    assert page.input.value == ""
    assert page.messages == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("new_content", "is_mine"),
    [
        ("Exact approved message", False),
        ("A different new outbound message", True),
    ],
    ids=["matching-inbound", "different-outbound"],
)
async def test_approved_message_stops_if_history_changes_during_send_resolution(
    monkeypatch,
    tmp_path,
    new_content: str,
    is_mine: bool,
) -> None:
    page = _MessagePage()
    page.on_send_resolution = lambda: page.messages.append(
        _MessageElement(new_content, is_mine=is_mine)
    )
    monkeypatch.setattr(messages, "get_browser", lambda: _Browser(page))
    params = messages.SendMessageParams(
        room_url="https://www.upwork.com/nx/messages/room-1234567",
        room_id="room-1234567",
        contact_name="Alex Client",
        message="Exact approved message",
    )

    result = await messages.send_message(
        _approved_once(params, messages.message_payload(params), monkeypatch, tmp_path)
    )

    assert result["status"] == "message_history_changed"
    assert result["external_action_taken"] is False
    assert result["composer_restored"] is True
    assert page.input.value == ""
    assert page.send_clicks == 0


@pytest.mark.asyncio
async def test_unrestorable_history_race_draft_is_terminal_and_action_remains_one_shot(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("UPWORK_MCP_STATE_DIR", str(tmp_path))
    page = _MessagePage()
    page.input.fail_restore = True
    page.on_send_resolution = lambda: page.messages.append(
        _MessageElement("A new inbound message", is_mine=False)
    )
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

    assert result["status"] == "draft_state_unknown"
    assert result["preclick_failure_status"] == "message_history_changed"
    assert result["external_action_taken"] is True
    assert result["composer_restored"] is False
    assert page.input.value == "Exact approved message"
    assert page.send_clicks == 0
    replay = await messages.send_message(params)
    assert replay["status"] == "approval_required"
    assert "already been claimed" in replay["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_field", ["contact", "room"])
async def test_approved_message_stops_if_identity_changes_during_send_resolution(
    monkeypatch,
    tmp_path,
    changed_field: str,
) -> None:
    page = _MessagePage()

    def change_identity() -> None:
        if changed_field == "contact":
            page.contact_name = "Different Client"
        else:
            page.url = "https://www.upwork.com/nx/messages/different-room-123"

    page.on_send_resolution = change_identity
    monkeypatch.setattr(messages, "get_browser", lambda: _Browser(page))
    params = messages.SendMessageParams(
        room_url="https://www.upwork.com/nx/messages/room-1234567",
        room_id="room-1234567",
        contact_name="Alex Client",
        message="Exact approved message",
    )

    result = await messages.send_message(
        _approved_once(params, messages.message_payload(params), monkeypatch, tmp_path)
    )

    assert result["status"] == "live_identity_mismatch"
    assert result["external_action_taken"] is False
    assert result["composer_restored"] is True
    assert page.input.value == ""
    assert page.send_clicks == 0


@pytest.mark.asyncio
async def test_unrestorable_preclick_draft_is_terminal_and_action_remains_one_shot(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("UPWORK_MCP_STATE_DIR", str(tmp_path))
    page = _MessagePage(scoped_send_available=False)
    page.input.fail_restore = True
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

    assert result["status"] == "draft_state_unknown"
    assert result["preclick_failure_status"] == "send_control_unavailable"
    assert result["external_action_taken"] is True
    assert result["composer_restored"] is False
    assert page.input.value == "Exact approved message"
    replay = await messages.send_message(params)
    assert replay["status"] == "approval_required"
    assert "already been claimed" in replay["message"]


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


class _WithdrawalDialog(_TextElement):
    def __init__(
        self,
        *,
        confirm_label: str = "Withdraw proposal",
        reason: _Input | None = None,
    ) -> None:
        super().__init__("Withdraw proposal")
        self.reason = reason
        self.confirm_clicks = 0
        self.confirm = _Button(
            lambda: setattr(self, "confirm_clicks", self.confirm_clicks + 1),
            confirm_label,
        )

    async def query_selector_all(self, selector: str) -> list[Any]:
        if "textarea" in selector:
            return [self.reason] if self.reason is not None else []
        if "confirm-withdraw" in selector or "button" in selector:
            return [self.confirm]
        return []


class _WithdrawalPage:
    def __init__(self, dialogs: list[_WithdrawalDialog]) -> None:
        self.url = "https://www.upwork.com/nx/proposals/1111111111111111111"
        self.title = "Google Ads review"
        self.status = "active"
        self.dialogs = dialogs
        self.initial_clicks = 0
        self.change_title_on_open = False
        self.initial = _Button(self._open_dialog, "Withdraw")

    def _open_dialog(self) -> None:
        self.initial_clicks += 1
        if self.change_title_on_open:
            self.title = "Different job"

    async def query_selector(self, selector: str):
        if selector == "body":
            withdrawn = any(dialog.confirm_clicks for dialog in self.dialogs)
            return _TextElement("Proposal was withdrawn" if withdrawn else "Active proposal")
        return None

    async def query_selector_all(self, selector: str) -> list[Any]:
        if "withdraw-button" in selector:
            return [self.initial]
        if 'role="dialog"' in selector or "withdraw-proposal-dialog" in selector:
            return self.dialogs
        if 'data-test="job-title"' in selector:
            return [_TextElement(self.title)]
        if "proposal-status" in selector:
            return [_TextElement(self.status)]
        return []


class _SpoofedWithdrawalPage(_WithdrawalPage):
    """Body copy changes after click, but the scoped owner status stays active."""

    async def goto(self, url: str, **_kwargs) -> None:
        self.url = url

    async def query_selector(self, selector: str):
        if selector == "body":
            return await super().query_selector(selector)
        if "job-title" in selector or "h1" in selector:
            return _TextElement(self.title)
        return None


def _withdrawal_params(*, reason: str | None = None) -> proposals.WithdrawProposalParams:
    return proposals.WithdrawProposalParams(
        proposal_url="https://www.upwork.com/nx/proposals/1111111111111111111",
        proposal_id="1111111111111111111",
        job_title="Google Ads review",
        proposal_status="active",
        reason=reason,
    )


def _mock_current_withdrawal_details(monkeypatch) -> None:
    async def details(_proposal_url: str, _page) -> dict[str, Any]:
        return {
            "url": "https://www.upwork.com/nx/proposals/1111111111111111111",
            "proposal_id": "1111111111111111111",
            "job_title": "Google Ads review",
            "status": "active",
        }

    monkeypatch.setattr(proposals, "_get_proposal_details_on_page", details)


@pytest.mark.asyncio
async def test_withdrawal_never_uses_generic_confirm_control(monkeypatch) -> None:
    _mock_current_withdrawal_details(monkeypatch)
    dialog = _WithdrawalDialog(confirm_label="Confirm")
    page = _WithdrawalPage([dialog])

    result = await proposals._withdraw_proposal_on_page(_withdrawal_params(), page)

    assert result["status"] == "error"
    assert "exact final Withdraw" in result["message"]
    assert result["external_action_taken"] is False
    assert page.initial_clicks == 1
    assert dialog.confirm_clicks == 0


@pytest.mark.asyncio
async def test_withdrawal_rejects_multiple_visible_withdrawal_dialogs(monkeypatch) -> None:
    _mock_current_withdrawal_details(monkeypatch)
    dialogs = [_WithdrawalDialog(), _WithdrawalDialog()]
    page = _WithdrawalPage(dialogs)

    result = await proposals._withdraw_proposal_on_page(_withdrawal_params(), page)

    assert result["status"] == "error"
    assert "one exact visible withdrawal dialog" in result["message"].casefold()
    assert result["external_action_taken"] is False
    assert all(dialog.confirm_clicks == 0 for dialog in dialogs)


@pytest.mark.asyncio
async def test_withdrawal_rechecks_target_identity_before_exact_confirm(monkeypatch) -> None:
    _mock_current_withdrawal_details(monkeypatch)
    dialog = _WithdrawalDialog()
    page = _WithdrawalPage([dialog])
    page.change_title_on_open = True

    result = await proposals._withdraw_proposal_on_page(_withdrawal_params(), page)

    assert result["status"] == "live_identity_mismatch"
    assert result["external_action_taken"] is False
    assert dialog.confirm_clicks == 0


@pytest.mark.asyncio
async def test_withdrawal_requires_exact_dialog_scoped_reason_readback(monkeypatch) -> None:
    _mock_current_withdrawal_details(monkeypatch)
    reason = _Input()
    reason.corrupt_readback = True
    dialog = _WithdrawalDialog(reason=reason)
    page = _WithdrawalPage([dialog])

    result = await proposals._withdraw_proposal_on_page(
        _withdrawal_params(reason="Specific approved reason"),
        page,
    )

    assert result["status"] == "error"
    assert "reason" in result["message"].casefold()
    assert result["external_action_taken"] is False
    assert dialog.confirm_clicks == 0


@pytest.mark.asyncio
async def test_withdrawal_exact_dialog_reason_and_control_are_read_back(monkeypatch) -> None:
    reason = _Input()
    dialog = _WithdrawalDialog(reason=reason)
    page = _WithdrawalPage([dialog])

    async def details(_proposal_url: str, _page) -> dict[str, Any]:
        return {
            "url": page.url,
            "proposal_id": "1111111111111111111",
            "job_title": "Google Ads review",
            "status": "withdrawn" if dialog.confirm_clicks else "active",
        }

    monkeypatch.setattr(proposals, "_get_proposal_details_on_page", details)
    result = await proposals._withdraw_proposal_on_page(
        _withdrawal_params(reason="Specific approved reason"),
        page,
    )

    assert result["status"] == "withdrawn"
    assert result["owner_system_readback"]["confirmed"] is True
    assert result["external_action_taken"] is True
    assert reason.value == "Specific approved reason"
    assert dialog.confirm_clicks == 1


@pytest.mark.asyncio
async def test_withdrawal_body_spoof_cannot_confirm_active_scoped_status(
    monkeypatch,
) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(proposals.asyncio, "sleep", no_sleep)
    dialog = _WithdrawalDialog()
    page = _SpoofedWithdrawalPage([dialog])

    result = await proposals._withdraw_proposal_on_page(_withdrawal_params(), page)

    assert result["status"] == "unknown"
    assert result["owner_system_readback"]["confirmed"] is False
    assert result["owner_system_readback"]["proposal_identity"][
        "proposal_status"
    ] == "active"
    assert dialog.confirm_clicks == 1
    assert (await page.query_selector("body")).text == "Proposal was withdrawn"


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
        if not self.form_open and "apply-button" in selector:
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

            return [_Button(open_form, "Apply Now")]
        if self.form_open and selector == proposals._BASE_CONNECTS_CONTROL_SELECTOR:
            return [_TextElement("12 Connects required to submit")]
        if self.form_open and selector == proposals._BOOST_AUCTION_CONTROL_SELECTOR:
            return [_TextElement("Boost your proposal auction: top bid 8 Connects")]
        if self.form_open and selector == proposals._PROFILE_HIGHLIGHT_OPENER:
            return [
                _Button(
                    lambda: setattr(self, "highlight_open", True),
                    "Add a portfolio project",
                )
            ]
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
        if self.highlight_open and "aria-label" in selector and "Close" in selector:
            def close_highlights() -> None:
                if self.dismiss_highlight:
                    self.highlight_open = False

            return [_Button(close_highlights, "Close")]
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
    assert result["base_connects_status"] == "complete"
    assert result["screening_questions"] == ["What similar work have you done?"]
    assert result["screening_questions_status"] == "complete"
    assert result["duration_options"] == [
        "Less than 1 month",
        "1 to 3 months",
        "3 to 6 months",
        "More than 6 months",
    ]
    assert result["duration_options_status"] == "complete"
    assert result["fee_net_text"] == []
    assert result["fee_net_status"] == "unavailable"
    assert result["fee_net_price_amount"] is None
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

    unavailable = await proposals._inspect_rate_increase_control(
        _RateInspectionPage(None),
        "hourly",
    )
    assert unavailable["status"] == "unavailable"


@pytest.mark.asyncio
async def test_live_fee_and_boost_context_helpers_normalize_and_classify_exact_state() -> None:
    fee = proposals._inspect_fee_net_context(
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

    unrelated_net_copy = proposals._inspect_fee_net_context(
        "Upwork service fee $6.30\nWe need net new leads",
    )
    assert unrelated_net_copy["text"] == ["Upwork service fee $6.30"]
    assert unrelated_net_copy["status"] == "incomplete"

    generic_bid = proposals._inspect_boost_auction_context(
        "Boost your proposal\nPlace a bid",
    )
    assert generic_bid["status"] == "incomplete"
    generic_connect_amount = proposals._inspect_boost_auction_context(
        "Boost your proposal with 8 Connects",
    )
    assert generic_connect_amount["status"] == "incomplete"
    generic_top_bid = proposals._inspect_boost_auction_context(
        "Boost your proposal\nTop bid unavailable",
    )
    assert generic_top_bid["status"] == "incomplete"
    live_auction = proposals._inspect_boost_auction_context(
        "Boost your proposal auction: top bid 8 Connects",
    )
    assert live_auction["status"] == "complete"
    no_bids = proposals._inspect_boost_auction_context(
        "Boost your proposal\nNo bids yet",
    )
    assert no_bids["status"] == "complete"


@pytest.mark.asyncio
async def test_commercial_and_connect_evidence_ignores_spoofed_body_copy() -> None:
    class _ScopedPage:
        def __init__(self, controls: dict[str, list[_TextElement]] | None = None) -> None:
            self.controls = controls or {}

        async def query_selector(self, selector: str):
            if selector == "body":
                return _TextElement(
                    "Upwork service fee $6.30\nYou'll receive $56.70\n"
                    "Boost auction: top bid 8 Connects\n12 Connects required to submit"
                )
            return None

        async def query_selector_all(self, selector: str) -> list[Any]:
            return self.controls.get(selector, [])

    spoofed = _ScopedPage()
    fee = await proposals._inspect_fee_net_state(spoofed)
    boost = await proposals._inspect_boost_auction_state(spoofed)
    base = await proposals._inspect_base_connects_state(spoofed)
    assert fee["status"] == "unavailable"
    assert boost["status"] == "unavailable"
    assert base["status"] == "unavailable"
    assert base["value"] is None

    scoped = _ScopedPage(
        {
            proposals._FEE_CONTROL_SELECTOR: [_TextElement("$6.30")],
            proposals._NET_CONTROL_SELECTOR: [_TextElement("$56.70")],
            proposals._BOOST_AUCTION_CONTROL_SELECTOR: [
                _TextElement("Boost auction: top bid 8 Connects")
            ],
            proposals._BASE_CONNECTS_CONTROL_SELECTOR: [
                _TextElement("12 Connects required to submit")
            ],
        }
    )
    assert (await proposals._inspect_fee_net_state(scoped))["status"] == "complete"
    assert (await proposals._inspect_boost_auction_state(scoped))["status"] == "complete"
    assert (await proposals._inspect_base_connects_state(scoped))["value"] == 12

    duplicated = _ScopedPage(
        {
            proposals._FEE_CONTROL_SELECTOR: [_TextElement("$6.30"), _TextElement("$6.30")],
            proposals._NET_CONTROL_SELECTOR: [_TextElement("$56.70")],
        }
    )
    assert (await proposals._inspect_fee_net_state(duplicated))["status"] == "incomplete"

    amount_missing = _ScopedPage(
        {
            proposals._FEE_CONTROL_SELECTOR: [_TextElement("Upwork service fee")],
            proposals._NET_CONTROL_SELECTOR: [_TextElement("You'll receive")],
        }
    )
    assert (await proposals._inspect_fee_net_state(amount_missing))["status"] == "incomplete"


class _DynamicText(_TextElement):
    def __init__(self, reader: Callable[[], str]) -> None:
        super().__init__()
        self.reader = reader

    async def text_content(self) -> str:
        return self.reader()


class _CommercialPrice(_Input):
    def __init__(self, page: _CommercialPreflightPage) -> None:
        super().__init__()
        self.page = page
        self.value = "50"

    async def fill(self, value: str) -> None:
        self.value = value
        self.page.price = Decimal(value)

    async def press(self, _key: str) -> None:
        return None


class _CommercialPreflightPage:
    def __init__(self) -> None:
        self.url = "https://www.upwork.com/nx/proposals/job/~123/apply"
        self.price = Decimal("50")
        self.price_control = _CommercialPrice(self)
        self.submit_queries = 0

    async def goto(self, url: str, **_kwargs: Any) -> None:
        self.url = url

    async def wait_for_timeout(self, _milliseconds: int) -> None:
        return None

    async def query_selector(self, selector: str):
        if selector == "body":
            return _TextElement("Hourly contract")
        if '[data-test="job-title"]' in selector:
            return _TextElement("Google Ads audit")
        return None

    async def query_selector_all(self, selector: str) -> list[Any]:
        if selector == proposals._HOURLY_RATE_INPUT_SELECTOR:
            return [self.price_control]
        if selector == proposals._FEE_CONTROL_SELECTOR:
            return [_DynamicText(lambda: f"${self.price * Decimal('0.10'):.2f}")]
        if selector == proposals._NET_CONTROL_SELECTOR:
            return [_DynamicText(lambda: f"${self.price * Decimal('0.90'):.2f}")]
        if selector.startswith('button[data-test="submit-proposal"]'):
            self.submit_queries += 1
        return []


class _HiddenCommercialPrice(_CommercialPrice):
    async def is_visible(self) -> bool:
        return False


@pytest.mark.asyncio
async def test_commercial_preflight_ignores_hidden_enabled_price_clone() -> None:
    page = _CommercialPreflightPage()
    hidden = _HiddenCommercialPrice(page)
    original_query = page.query_selector_all

    async def with_hidden_clone(selector: str) -> list[Any]:
        if selector == proposals._HOURLY_RATE_INPUT_SELECTOR:
            return [hidden, page.price_control]
        return await original_query(selector)

    page.query_selector_all = with_hidden_clone  # type: ignore[method-assign]
    result = await proposals._inspect_proposal_commercial_preflight_on_page(
        proposals.InspectProposalCommercialPreflightParams(
            job_url="https://www.upwork.com/nx/proposals/job/~123/apply",
            rate=63,
        ),
        page,
    )

    assert result["fee_net_status"] == "complete"
    assert result["price_restored"] is True
    assert hidden.value == "50"


@pytest.mark.asyncio
async def test_commercial_preflight_rejects_fee_snapshot_stale_from_different_price() -> None:
    class _StaleFeePage(_CommercialPreflightPage):
        async def query_selector_all(self, selector: str) -> list[Any]:
            if selector == proposals._FEE_CONTROL_SELECTOR:
                return [_TextElement("$5.00")]
            if selector == proposals._NET_CONTROL_SELECTOR:
                return [_TextElement("$45.00")]
            return await super().query_selector_all(selector)

    page = _StaleFeePage()
    result = await proposals._inspect_proposal_commercial_preflight_on_page(
        proposals.InspectProposalCommercialPreflightParams(
            job_url="https://www.upwork.com/nx/proposals/job/~123/apply",
            rate=63,
        ),
        page,
    )

    assert result["fee_net_status"] == "incomplete"
    assert result["fee_net_price_amount"] is None
    assert result["fee_net_details"]["stale_original_price_evidence_rejected"] is True
    assert result["price_restored"] is True


@pytest.mark.asyncio
async def test_commercial_preflight_rejects_fee_and_net_that_do_not_equal_gross() -> None:
    class _WrongGrossPage(_CommercialPreflightPage):
        async def query_selector_all(self, selector: str) -> list[Any]:
            if selector == proposals._FEE_CONTROL_SELECTOR:
                return [
                    _DynamicText(
                        lambda: "$5.00" if self.price == Decimal("50") else "$6.00"
                    )
                ]
            if selector == proposals._NET_CONTROL_SELECTOR:
                return [
                    _DynamicText(
                        lambda: "$45.00" if self.price == Decimal("50") else "$54.00"
                    )
                ]
            return await super().query_selector_all(selector)

    result = await proposals._inspect_proposal_commercial_preflight_on_page(
        proposals.InspectProposalCommercialPreflightParams(
            job_url="https://www.upwork.com/nx/proposals/job/~123/apply",
            rate=63,
        ),
        _WrongGrossPage(),
    )

    reconciliation = result["fee_net_details"]["gross_reconciliation"]
    assert reconciliation["fee_amount"] == "6.00"
    assert reconciliation["net_amount"] == "54.00"
    assert reconciliation["fee_plus_net"] == "60.00"
    assert reconciliation["approved_gross"] == "63.00"
    assert reconciliation["gross_matches"] is False
    assert result["fee_net_status"] == "incomplete"
    assert result["fee_net_price_amount"] is None
    assert result["price_restored"] is True


def test_exact_currency_amount_ignores_percent_but_rejects_multiple_prices() -> None:
    assert proposals._exact_currency_amount("10% fee $6.30") == (Decimal("6.30"), "$")
    assert proposals._exact_currency_amount("$6.30 or $7.00") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_evidence", ["ambiguous", "mixed_currency"])
async def test_commercial_preflight_rejects_ambiguous_or_mixed_currency_amounts(
    bad_evidence: str,
) -> None:
    class _BadCurrencyPage(_CommercialPreflightPage):
        async def query_selector_all(self, selector: str) -> list[Any]:
            if selector == proposals._FEE_CONTROL_SELECTOR:
                if bad_evidence == "ambiguous":
                    return [
                        _DynamicText(
                            lambda: f"${self.price * Decimal('0.10'):.2f} or "
                            f"${self.price * Decimal('0.11'):.2f}"
                        )
                    ]
                return [_DynamicText(lambda: f"${self.price * Decimal('0.10'):.2f}")]
            if selector == proposals._NET_CONTROL_SELECTOR:
                symbol = "$" if bad_evidence == "ambiguous" else "£"
                return [
                    _DynamicText(lambda: f"{symbol}{self.price * Decimal('0.90'):.2f}")
                ]
            return await super().query_selector_all(selector)

    result = await proposals._inspect_proposal_commercial_preflight_on_page(
        proposals.InspectProposalCommercialPreflightParams(
            job_url="https://www.upwork.com/nx/proposals/job/~123/apply",
            rate=63,
        ),
        _BadCurrencyPage(),
    )

    reconciliation = result["fee_net_details"]["gross_reconciliation"]
    assert reconciliation["gross_matches"] is False
    if bad_evidence == "ambiguous":
        assert reconciliation["amounts_unambiguous"] is False
    else:
        assert reconciliation["same_currency"] is False
    assert result["fee_net_status"] == "incomplete"
    assert result["fee_net_price_amount"] is None
    assert result["price_restored"] is True


@pytest.mark.asyncio
async def test_commercial_preflight_rejects_unstable_exact_restoration() -> None:
    class _UnstableRestorePrice(_CommercialPrice):
        def __init__(self, page: _CommercialPreflightPage) -> None:
            super().__init__(page)
            self.restoring = False
            self.restore_reads = 0

        async def fill(self, value: str) -> None:
            if value == "50" and self.value != "50":
                self.restoring = True
                self.restore_reads = 0
            await super().fill(value)

        async def input_value(self) -> str:
            if self.restoring:
                self.restore_reads += 1
                if self.restore_reads >= 2:
                    return "50.00"
            return self.value

    page = _CommercialPreflightPage()
    page.price_control = _UnstableRestorePrice(page)
    result = await proposals._inspect_proposal_commercial_preflight_on_page(
        proposals.InspectProposalCommercialPreflightParams(
            job_url="https://www.upwork.com/nx/proposals/job/~123/apply",
            rate=63,
        ),
        page,
    )

    assert result["fee_net_status"] == "incomplete"
    assert result["fee_net_source"] is None
    assert result["price_restored"] is False
    assert result["external_action_taken"] is True


@pytest.mark.asyncio
async def test_commercial_preflight_binds_entered_price_and_restores_original() -> None:
    page = _CommercialPreflightPage()
    result = await proposals._inspect_proposal_commercial_preflight_on_page(
        proposals.InspectProposalCommercialPreflightParams(
            job_url="https://www.upwork.com/nx/proposals/job/~123/apply",
            rate=63,
        ),
        page,
    )

    assert result["fee_net_status"] == "complete"
    assert result["fee_net_price_amount"] == "63.00"
    assert result["fee_net_source"] == "scoped_reversible_price_preflight"
    assert result["fee_net_details"]["gross_reconciliation"]["gross_matches"] is True
    assert result["price_restored"] is True
    assert result["external_action_taken"] is False
    assert page.price_control.value == "50"
    assert page.submit_queries == 0

    class _FixedRadio:
        def __init__(self, checked: bool) -> None:
            self.checked = checked

        async def is_checked(self) -> bool:
            return self.checked

        async def is_visible(self) -> bool:
            return True

        async def is_enabled(self) -> bool:
            return True

        async def get_attribute(self, _name: str) -> None:
            return None

    class _FixedSection(_TextElement):
        def __init__(self, fixed_page: _CommercialPreflightPage) -> None:
            super().__init__("By project By milestone")
            self.project = _FixedRadio(True)
            self.milestone = _FixedRadio(False)
            self.amount = _CommercialPrice(fixed_page)
            self.amount.value = "400"
            fixed_page.price = Decimal("400")

        async def query_selector_all(self, selector: str) -> list[Any]:
            if selector == proposals._BY_PROJECT_AMOUNT_INPUT_SELECTOR:
                return [self.amount]
            if "By project" in selector or 'value="project"' in selector:
                return [self.project]
            if "By milestone" in selector or 'value="milestone"' in selector:
                return [self.milestone]
            return []

    class _FixedPreflightPage(_CommercialPreflightPage):
        def __init__(self) -> None:
            super().__init__()
            self.section = _FixedSection(self)

        async def query_selector(self, selector: str):
            if selector == "body":
                return _TextElement("Fixed-price project\nBy project\nBy milestone")
            return await super().query_selector(selector)

        async def query_selector_all(self, selector: str) -> list[Any]:
            if "fieldset:has" in selector and "By project" in selector:
                return [self.section]
            return await super().query_selector_all(selector)

    fixed_page = _FixedPreflightPage()
    fixed = await proposals._inspect_proposal_commercial_preflight_on_page(
        proposals.InspectProposalCommercialPreflightParams(
            job_url="https://www.upwork.com/nx/proposals/job/~123/apply",
            bid=500,
            payment_structure="by_project",
        ),
        fixed_page,
    )
    assert fixed["job_type"] == "fixed"
    assert fixed["fee_net_status"] == "complete"
    assert fixed["fee_net_price_amount"] == "500.00"
    assert fixed["price_restored"] is True
    assert fixed_page.section.amount.value == "400"

    failing_restore_page = _CommercialPreflightPage()
    original_fill = failing_restore_page.price_control.fill
    fill_count = 0

    async def fail_second_fill(value: str) -> None:
        nonlocal fill_count
        fill_count += 1
        if fill_count > 1:
            raise RuntimeError("restore failed")
        await original_fill(value)

    failing_restore_page.price_control.fill = fail_second_fill  # type: ignore[method-assign]
    discarded = await proposals._inspect_proposal_commercial_preflight_on_page(
        proposals.InspectProposalCommercialPreflightParams(
            job_url="https://www.upwork.com/nx/proposals/job/~123/apply",
            rate=63,
        ),
        failing_restore_page,
    )
    assert discarded["fee_net_status"] == "incomplete"
    assert discarded["fee_net_source"] is None
    assert discarded["fee_net_price_amount"] is None
    assert discarded["price_restored"] is False
    assert discarded["external_action_taken"] is True

    with pytest.raises(ValidationError, match="by-project"):
        proposals.InspectProposalCommercialPreflightParams(
            job_url="https://www.upwork.com/jobs/~123",
            bid=500,
        )
    with pytest.raises(ValidationError, match="greater than or equal to 50"):
        proposals.InspectProposalCommercialPreflightParams(
            job_url="https://www.upwork.com/jobs/~123",
            rate=49,
        )


@pytest.mark.asyncio
async def test_proposal_inspection_never_clicks_accept_interview(monkeypatch) -> None:
    class _InvitePage:
        def __init__(self) -> None:
            self.url = "https://www.upwork.com/jobs/~123"
            self.accept_clicks = 0

        async def goto(self, url: str, **_kwargs: Any) -> None:
            self.url = url

        async def query_selector(self, selector: str):
            if selector == "body":
                return _TextElement("Invitation to apply\nHourly contract")
            if "Accept Interview" in selector or (
                'data-test="apply-button"' in selector
                and ':text-is("Apply Now")' not in selector
            ):
                return _Button(
                    lambda: setattr(self, "accept_clicks", self.accept_clicks + 1),
                    "Accept Interview",
                )
            return None

        async def query_selector_all(self, _selector: str) -> list[Any]:
            return []

    page = _InvitePage()
    monkeypatch.setattr(proposals, "get_browser", lambda: _Browser(page))
    result = await proposals.inspect_proposal_form("https://www.upwork.com/jobs/~123")

    assert result["form_status"] == "unavailable"
    assert result["external_action_taken"] is False
    assert page.accept_clicks == 0


@pytest.mark.asyncio
async def test_unreadable_visibility_never_counts_as_visible() -> None:
    class _UnreadableVisibility:
        async def is_visible(self) -> bool:
            raise RuntimeError("detached")

    assert await proposals._element_is_visible(_UnreadableVisibility()) is False
