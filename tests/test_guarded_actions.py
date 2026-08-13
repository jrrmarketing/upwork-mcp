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
            reason="   ",
        )


@pytest.mark.asyncio
async def test_submit_proposal_requires_exact_approval_before_browser(monkeypatch) -> None:
    monkeypatch.setattr(proposals, "get_browser", _browser_must_not_open)
    params = proposals.SubmitProposalParams(
        job_url="https://www.upwork.com/jobs/~123",
        cover_letter="Exact approved copy",
        rate=63,
        duration="1 to 3 months",
        base_connects=12,
    )

    prepared = await proposals.submit_proposal(params)
    assert prepared["status"] == "approval_required"
    assert prepared["external_action_taken"] is False
    assert prepared["approval_sha256"] == proposals.approval_payload_digest(
        proposals.proposal_submission_payload(params)
    )

    mismatch = await proposals.submit_proposal(
        params.model_copy(update={"approved": True, "approval_sha256": "0" * 64})
    )
    assert mismatch["status"] == "approval_mismatch"
    assert mismatch["external_action_taken"] is False


@pytest.mark.asyncio
async def test_approved_proposal_still_requires_live_preflight_before_browser(monkeypatch) -> None:
    monkeypatch.setattr(proposals, "get_browser", _browser_must_not_open)
    params = proposals.SubmitProposalParams(
        job_url="https://www.upwork.com/jobs/~123",
        cover_letter="Exact approved copy",
        rate=63,
        duration="1 to 3 months",
    )
    params = _approved(params, proposals.proposal_submission_payload(params))

    result = await proposals.submit_proposal(params)
    assert result["status"] == "preflight_required"
    assert result["external_action_taken"] is False


@pytest.mark.asyncio
async def test_one_time_prepared_action_rejects_changed_terms_before_browser(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("UPWORK_MCP_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(proposals, "get_browser", _browser_must_not_open)
    original = proposals.SubmitProposalParams(
        job_url="https://www.upwork.com/jobs/~123",
        cover_letter="Exact approved copy",
        rate=63,
        duration="1 to 3 months",
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
async def test_legacy_withdrawal_call_is_prepare_only(monkeypatch) -> None:
    monkeypatch.setattr(proposals, "get_browser", _browser_must_not_open)
    result = await proposals.withdraw_proposal("https://www.upwork.com/ab/proposals/123")
    assert result["status"] == "approval_required"
    assert result["exact_payload"] == {
        "proposal_url": "https://www.upwork.com/ab/proposals/123",
        "reason": None,
    }
    assert result["external_action_taken"] is False


@pytest.mark.asyncio
async def test_message_requires_exact_approval_before_browser(monkeypatch) -> None:
    monkeypatch.setattr(messages, "get_browser", _browser_must_not_open)
    params = messages.SendMessageParams(room_id="room-123", message="Exact approved message")

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
        invitation_url="https://www.upwork.com/ab/proposals/job/~123/apply/",
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
    def __init__(self) -> None:
        self.url = ""
        self.messages: list[_MessageElement] = []
        self.input = _Input()

    async def goto(self, url: str, **_kwargs) -> None:
        self.url = url

    async def query_selector_all(self, selector: str) -> list[Any]:
        if "message" in selector:
            return self.messages
        return []

    async def query_selector(self, selector: str):
        if "message-input" in selector or "textarea" in selector:
            return self.input
        if "send-button" in selector or "Send" in selector:
            def send() -> None:
                self.messages.append(_MessageElement(self.input.value, is_mine=True))
                self.input.value = ""

            return _Button(send)
        return None


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
    params = messages.SendMessageParams(room_id="room-123", message="Exact approved message")
    params = _approved(params, messages.message_payload(params))

    result = await messages.send_message(params)
    assert result["status"] == "sent"
    assert result["external_action_taken"] is True
    assert result["owner_system_readback"]["confirmed"] is True
    assert result["owner_system_readback"]["exact_visible_copy"] is True


class _InspectPage:
    def __init__(self) -> None:
        self.url = "https://www.upwork.com/jobs/~123"
        self.form_open = False
        self.body = "Google Ads audit job"

    async def goto(self, url: str, **_kwargs) -> None:
        self.url = url

    async def wait_for_load_state(self, _state: str) -> None:
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
        return None

    async def query_selector_all(self, selector: str) -> list[Any]:
        if "screening-question" in selector:
            return [_TextElement("What similar work have you done?")]
        return []


@pytest.mark.asyncio
async def test_inspect_proposal_form_is_read_only_and_returns_live_fields(monkeypatch) -> None:
    page = _InspectPage()
    monkeypatch.setattr(proposals, "get_browser", lambda: _Browser(page))

    result = await proposals.inspect_proposal_form("https://www.upwork.com/jobs/~123")
    assert result["form_status"] == "ready"
    assert result["job_type"] == "fixed"
    assert result["base_connects"] == 12
    assert result["screening_questions"] == ["What similar work have you done?"]
    assert result["duration_options"] == [
        "Less than 1 month",
        "1 to 3 months",
        "3 to 6 months",
        "More than 6 months",
    ]
    assert result["fee_net_text"]
    assert result["boost_auction_text"]
    assert result["external_action_taken"] is False
