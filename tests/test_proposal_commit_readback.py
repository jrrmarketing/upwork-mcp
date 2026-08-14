"""Fail-closed readback tests for consequential proposal controls."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from upwork_mcp.tools import proposals


class _Text:
    def __init__(self, text: str) -> None:
        self.text = text

    async def text_content(self) -> str:
        return self.text

    async def is_visible(self) -> bool:
        return True


class _Field:
    def __init__(self, *, wrong_readback: str | None = None, visible: bool = True) -> None:
        self.value = ""
        self.wrong_readback = wrong_readback
        self.visible = visible

    async def is_enabled(self) -> bool:
        return True

    async def is_visible(self) -> bool:
        return self.visible

    async def fill(self, value: str) -> None:
        self.value = value

    async def input_value(self) -> str:
        return self.wrong_readback if self.wrong_readback is not None else self.value


class _Select:
    def __init__(self, *, wrong_readback: str | None = None, visible: bool = True) -> None:
        self.label = ""
        self.wrong_readback = wrong_readback
        self.visible = visible

    async def is_enabled(self) -> bool:
        return True

    async def is_visible(self) -> bool:
        return self.visible

    async def select_option(self, *, label: str) -> None:
        self.label = label

    async def evaluate(self, _script: str) -> str:
        return self.wrong_readback if self.wrong_readback is not None else self.label


class _Button(_Text):
    def __init__(self, text: str, callback=None) -> None:
        super().__init__(text)
        self.callback = callback

    async def is_enabled(self) -> bool:
        return True

    async def is_visible(self) -> bool:
        return True

    async def click(self) -> None:
        if self.callback:
            self.callback()


class _HiddenButton(_Button):
    async def is_visible(self) -> bool:
        return False


class _ControlScope:
    def __init__(self, controls: list[Any]) -> None:
        self.controls = controls

    async def query_selector_all(self, _selector: str) -> list[Any]:
        return self.controls


@pytest.mark.asyncio
async def test_consequential_submit_controls_reject_hidden_clones_and_visible_ambiguity() -> None:
    visible = _Button("Submit proposal")
    hidden = _HiddenButton("Submit proposal")

    assert await proposals._first_stage_submit_control(_ControlScope([hidden, visible])) is visible
    assert await proposals._first_stage_submit_control(
        _ControlScope([visible, _Button("Submit proposal")])
    ) is None


@pytest.mark.asyncio
async def test_final_send_for_connects_ignores_hidden_clone_but_requires_one_visible_match() -> None:
    visible = _Button("Send for 12 Connects")
    hidden = _HiddenButton("Send for 12 Connects")

    assert await proposals._exact_final_send_control(
        _ControlScope([hidden, visible]), 12
    ) is visible
    assert await proposals._exact_final_send_control(
        _ControlScope([visible, _Button("Send for 12 Connects")]), 12
    ) is None


class _CommitPage:
    def __init__(self, mismatch: str | None = None) -> None:
        self.url = "https://www.upwork.com/nx/proposals/job/~abc123/apply"
        self.body = "Google Ads audit\nHourly contract\n12 Connects required to submit"
        self.rate = _Field(wrong_readback="64" if mismatch == "rate" else None)
        self.cover = _Field(wrong_readback="changed" if mismatch == "cover" else None)
        self.answer = _Field(wrong_readback="changed" if mismatch == "answer" else None)
        self.duration = _Select(wrong_readback="3 to 6 months" if mismatch == "duration" else None)
        self.increase = None if mismatch == "rate_status" else _Select()
        self.submit_queries = 0
        self.submit_clicks = 0

    async def goto(self, url: str, **_kwargs: Any) -> None:
        self.url = url

    async def query_selector(self, selector: str):
        if selector == "body":
            return _Text(self.body)
        if 'data-test="job-title"' in selector:
            return _Text("Google Ads audit")
        return None

    async def query_selector_all(self, selector: str) -> list[Any]:
        if selector.startswith('button[data-test="submit-proposal"]'):
            self.submit_queries += 1
            return [
                _Button(
                    "Submit proposal",
                    lambda: setattr(self, "submit_clicks", self.submit_clicks + 1),
                )
            ]
        if "hourly-rate-input" in selector:
            return [self.rate]
        if "cover-letter-input" in selector:
            return [self.cover]
        if "question-input" in selector:
            return [self.answer]
        if "screening-question" in selector:
            return [_Text("What similar work have you done?")]
        if 'select[name*="duration"]' in selector:
            return [self.duration]
        if 'select[name*="increase"]' in selector:
            return [self.increase] if self.increase else []
        if selector == proposals._BASE_CONNECTS_CONTROL_SELECTOR:
            return [_Text("12 Connects required to submit")]
        return []


def _params(**updates: Any) -> proposals.SubmitProposalParams:
    values: dict[str, Any] = {
        "job_url": "https://www.upwork.com/jobs/~abc123",
        "job_id": "~abc123",
        "form_url": "https://www.upwork.com/nx/proposals/job/~abc123/apply",
        "job_title": "Google Ads audit",
        "job_type": "hourly",
        "cover_letter": "Exact approved copy",
        "fee_net_text": [
            "Upwork service fee $6.30",
            "You'll receive $56.70 net",
        ],
        "fee_net_status": "complete",
        "fee_net_price_amount": "63.00",
        "fee_net_source": "scoped_reversible_price_preflight",
        "boost_auction_text": ["Boost your proposal 8 Connects"],
        "boost_auction_status": "complete",
        "rate": 63,
        "answers": ["Exact approved answer"],
        "screening_questions": ["What similar work have you done?"],
        "screening_questions_status": "complete",
        "duration": "1 to 3 months",
        "duration_options_status": "complete",
        "available_profile_highlights_status": "complete",
        "base_connects": 12,
        "base_connects_status": "complete",
        "rate_increase_control_status": "complete",
        "action_id": "uwa_test_action",
    }
    values.update(updates)
    return proposals.SubmitProposalParams(**values)


def _commercial_inspectors(monkeypatch, *, changed: str | None = None) -> None:
    async def fee(_page):
        text = (
            ["Changed fee"]
            if changed == "fee"
            else ["Upwork service fee $6.30", "You'll receive $56.70 net"]
        )
        return {"text": text, "status": "complete", "details": {}}

    async def boost(_page):
        text = ["Changed auction"] if changed == "auction" else ["Boost your proposal 8 Connects"]
        return {"text": text, "status": "complete", "details": {}}

    monkeypatch.setattr(proposals, "_inspect_fee_net_state", fee, raising=False)
    monkeypatch.setattr(proposals, "_inspect_boost_auction_state", boost, raising=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mismatch",
    ["rate", "cover", "answer", "duration", "rate_status", "fee", "auction"],
)
async def test_pre_submit_mismatch_never_queries_submit(
    monkeypatch,
    mismatch: str,
) -> None:
    page = _CommitPage(mismatch)
    _commercial_inspectors(
        monkeypatch,
        changed=mismatch if mismatch in {"fee", "auction"} else None,
    )

    result = await proposals._submit_proposal_on_page(_params(), page)

    assert result["status"] in {"error", "live_form_mismatch"}
    assert result["external_action_taken"] is False
    assert page.submit_queries == 0
    assert page.submit_clicks == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("hidden_control", ["rate", "cover", "answer"])
async def test_hidden_enabled_proposal_input_never_queries_submit(
    monkeypatch,
    hidden_control: str,
) -> None:
    page = _CommitPage()
    getattr(page, hidden_control).visible = False
    _commercial_inspectors(monkeypatch)

    result = await proposals._submit_proposal_on_page(_params(), page)

    assert result["status"] == "live_form_mismatch"
    assert result["external_action_taken"] is False
    assert page.submit_queries == 0
    assert page.submit_clicks == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("hidden_control", ["rate", "cover", "answer"])
async def test_control_hidden_before_final_readback_never_queries_submit(
    monkeypatch,
    hidden_control: str,
) -> None:
    page = _CommitPage()
    _commercial_inspectors(monkeypatch)

    async def hide_control(_page, _highlights):
        getattr(page, hidden_control).visible = False
        return True, None

    monkeypatch.setattr(proposals, "_select_profile_highlights", hide_control)
    result = await proposals._submit_proposal_on_page(_params(), page)

    assert result["status"] == "live_form_mismatch"
    assert result["external_action_taken"] is False
    assert page.submit_queries == 0
    assert page.submit_clicks == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("hidden_control", ["duration", "increase"])
async def test_hidden_enabled_duration_or_increase_never_queries_submit(
    monkeypatch,
    hidden_control: str,
) -> None:
    page = _CommitPage()
    control = getattr(page, hidden_control)
    assert control is not None
    control.visible = False
    _commercial_inspectors(monkeypatch)

    result = await proposals._submit_proposal_on_page(_params(), page)

    assert result["status"] in {"error", "live_form_mismatch"}
    assert result["external_action_taken"] is False
    assert page.submit_queries == 0
    assert page.submit_clicks == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("hidden_control", ["duration", "increase"])
async def test_duration_or_increase_hidden_before_final_readback_never_queries_submit(
    monkeypatch,
    hidden_control: str,
) -> None:
    page = _CommitPage()
    _commercial_inspectors(monkeypatch)

    async def hide_control(_page, _highlights):
        control = getattr(page, hidden_control)
        assert control is not None
        control.visible = False
        return True, None

    monkeypatch.setattr(proposals, "_select_profile_highlights", hide_control)
    result = await proposals._submit_proposal_on_page(_params(), page)

    assert result["status"] == "live_form_mismatch"
    assert result["external_action_taken"] is False
    assert page.submit_queries == 0
    assert page.submit_clicks == 0


@pytest.mark.asyncio
async def test_profile_highlight_mismatch_never_queries_submit(monkeypatch) -> None:
    page = _CommitPage()
    _commercial_inspectors(monkeypatch)

    async def mismatch(_page, _highlights):
        return False, "exact profile highlight mismatch"

    monkeypatch.setattr(proposals, "_select_profile_highlights", mismatch)
    result = await proposals._submit_proposal_on_page(_params(), page)
    assert result["status"] == "live_form_mismatch"
    assert page.submit_queries == 0


@pytest.mark.asyncio
async def test_silent_cover_reset_after_other_interactions_never_queries_submit(monkeypatch) -> None:
    page = _CommitPage()
    _commercial_inspectors(monkeypatch)

    async def reset_cover(_page, _highlights):
        page.cover.wrong_readback = ""
        return True, None

    monkeypatch.setattr(proposals, "_select_profile_highlights", reset_cover)
    result = await proposals._submit_proposal_on_page(_params(), page)

    assert result["status"] == "live_form_mismatch"
    assert "cover letter silently changed" in result["message"]
    assert result["external_action_taken"] is False
    assert page.submit_queries == 0
    assert page.submit_clicks == 0


@pytest.mark.asyncio
async def test_positive_boost_is_blocked_before_form_or_submit_interaction() -> None:
    with pytest.raises(ValidationError, match="positive boost"):
        _params(boost_connects=8)

    values = _params().model_dump()
    values["boost_connects"] = 8
    unsafe = proposals.SubmitProposalParams.model_construct(**values)
    page = _CommitPage()
    result = await proposals._submit_proposal_on_page(unsafe, page)

    assert result["status"] == "unsupported"
    assert result["external_action_taken"] is False
    assert page.submit_queries == 0
    assert page.submit_clicks == 0


@pytest.mark.asyncio
async def test_missing_required_highlight_tab_never_queries_submit(monkeypatch) -> None:
    page = _CommitPage()
    _commercial_inspectors(monkeypatch)
    chooser_page = _HighlightPage(
        {
            "portfolio": [_HighlightButton("Family Law Growth")],
            "certifications": [],
        }
    )
    select_highlights = proposals._select_profile_highlights

    async def inspect_missing_tabs(_page, highlights):
        return await select_highlights(chooser_page, highlights)

    monkeypatch.setattr(proposals, "_select_profile_highlights", inspect_missing_tabs)
    result = await proposals._submit_proposal_on_page(
        _params(profile_highlights=["Family Law Growth"]),
        page,
    )
    assert result["status"] == "live_form_mismatch"
    assert page.submit_queries == 0


@pytest.mark.asyncio
async def test_rate_increase_absence_requires_bound_not_applicable() -> None:
    class _Empty:
        async def query_selector_all(self, _selector: str) -> list[Any]:
            return []

    page = _Empty()
    assert await proposals._select_rate_increase_never(page, "complete") is False
    assert await proposals._select_rate_increase_never(page, None) is False
    assert await proposals._select_rate_increase_never(page, "not_applicable") is True


class _Choice(_Button):
    def __init__(self, *, readable: bool = True) -> None:
        super().__init__("Don't boost")
        self.selected = False
        self.readable = readable

    async def click(self) -> None:
        self.selected = True

    async def get_attribute(self, name: str) -> str | None:
        if name == "aria-pressed" and self.readable:
            return str(self.selected).lower()
        return None


class _BoostDialog(_Text):
    def __init__(
        self,
        *,
        boost_readback: str | None = None,
        choice: _Choice | None = None,
        send_label: str = "Send for 12 Connects",
    ) -> None:
        super().__init__("Boost your proposal with Connects")
        self.boost = _Field(wrong_readback=boost_readback)
        self.choice = choice
        self.send_queries = 0
        self.send = _Button(send_label)

    async def is_visible(self) -> bool:
        return True

    async def query_selector_all(self, selector: str) -> list[Any]:
        if 'input[name*="boost"]' in selector:
            return [self.boost]
        if "Don't boost" in selector or "No, thanks" in selector:
            return [self.choice] if self.choice else []
        if selector == "button":
            self.send_queries += 1
            return [self.send]
        return []


class _BoostPage:
    def __init__(self, dialog: _BoostDialog) -> None:
        self.dialog = dialog

    async def query_selector_all(self, selector: str) -> list[Any]:
        return [self.dialog] if selector == '[role="dialog"]' else []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("boost", "dialog"),
    [
        (8, _BoostDialog(boost_readback="7")),
        (0, _BoostDialog(choice=None)),
        (0, _BoostDialog(choice=_Choice(readable=False))),
    ],
)
async def test_boost_mismatch_never_queries_final_send(
    boost: int,
    dialog: _BoostDialog,
) -> None:
    send, error = await proposals._configure_boost_step(_BoostPage(dialog), boost, 12)
    assert send is None
    assert error
    assert dialog.send_queries == 0


@pytest.mark.asyncio
async def test_explicit_no_boost_is_read_back_before_exact_cost_scoped_send() -> None:
    unboosted = _BoostDialog(choice=_Choice())
    send, error = await proposals._configure_boost_step(_BoostPage(unboosted), 0, 12)
    assert (send, error) == (unboosted.send, None)
    assert unboosted.choice and unboosted.choice.selected is True

    wrong_cost = _BoostDialog(choice=_Choice(), send_label="Send for 999 Connects")
    send, error = await proposals._configure_boost_step(_BoostPage(wrong_cost), 0, 12)
    assert send is None
    assert "matching approved base Connects" in str(error)

    positive = _BoostDialog()
    send, error = await proposals._configure_boost_step(_BoostPage(positive), 8, 12)
    assert send is None
    assert "positive boost" in str(error)
    assert positive.send_queries == 0


@pytest.mark.asyncio
async def test_direct_submission_never_infers_connect_spend(monkeypatch) -> None:
    page = _CommitPage()
    _commercial_inspectors(monkeypatch)

    async def confirmed(*_args, **_kwargs):
        return {"confirmed": True, "proposal_id": "1111111111111111111"}

    monkeypatch.setattr(proposals, "_proposal_confirmation", confirmed)
    result = await proposals._submit_proposal_on_page(
        _params(),
        page,
    )
    assert page.submit_clicks == 1
    assert result["boost_spend_verified"] is False
    assert result["status"] == "submitted"
    assert result["approved_base_connects"] == 12
    assert result["connects_spend_verified"] is False
    assert "connects_used" not in result

    verified = proposals._confirmed_submission_result(
        params=_params(),
        readback={
            "confirmed": True,
            "connects_spend_verified": True,
            "connects_used": 12,
        },
    )
    assert verified["connects_spend_verified"] is True
    assert verified["connects_used"] == 12

    invalid = proposals._confirmed_submission_result(
        params=_params(),
        readback={
            "confirmed": True,
            "connects_spend_verified": True,
            "connects_used": True,
        },
    )
    assert invalid["connects_spend_verified"] is False
    assert "connects_used" not in invalid


class _HighlightButton(_Button):
    def __init__(self, title: str, *, selected: bool = False) -> None:
        super().__init__("Selected" if selected else "Select highlight")
        self.title = title
        self.selected = selected

    async def click(self) -> None:
        self.selected = True
        self.text = "Selected"

    async def evaluate(self, _script: str) -> str:
        return self.title

    async def get_attribute(self, _name: str) -> None:
        return None


class _HighlightTab(_Button):
    def __init__(self, identity: str, page: _HighlightPage) -> None:
        super().__init__(identity, lambda: setattr(page, "tab", identity))
        self.identity = identity

    async def get_attribute(self, name: str) -> str | None:
        return self.identity if name == "data-ev-tab" else None


class _HighlightChooser(_Text):
    def __init__(self, page: _HighlightPage) -> None:
        super().__init__("Add profile highlights")
        self.page = page

    async def is_visible(self) -> bool:
        return self.page.open

    async def query_selector_all(self, selector: str) -> list[Any]:
        if 'text-is("Add to highlights")' in selector:
            return [_Button("Add to highlights", lambda: setattr(self.page, "open", False))]
        return []


class _Keyboard:
    async def press(self, _key: str) -> None:
        return None


class _HighlightPage:
    def __init__(self, options: dict[str, list[_HighlightButton]]) -> None:
        self.options = options
        self.tab = next(iter(options))
        self.open = False
        self.keyboard = _Keyboard()
        self.chooser = _HighlightChooser(self)

    async def wait_for_selector(self, *_args, **_kwargs) -> None:
        return None

    async def wait_for_timeout(self, _milliseconds: int) -> None:
        return None

    async def query_selector(self, selector: str):
        if "Add profile highlights" in selector and self.open:
            return self.chooser
        if "aria-label" in selector and "Close" in selector and self.open:
            return _Button("Close", lambda: setattr(self, "open", False))
        return None

    async def query_selector_all(self, selector: str) -> list[Any]:
        if "Add a portfolio project" in selector or "Edit profile highlights" in selector:
            if not self.open:
                return [_Button("Add a portfolio project", lambda: setattr(self, "open", True))]
        if '[role="dialog"]:has-text("Add profile highlights")' in selector:
            return [self.chooser] if self.open else []
        if 'role="tab"' in selector and self.open:
            return [_HighlightTab(identity, self) for identity in self.options]
        if "Select highlight" in selector and self.open:
            return self.options[self.tab]
        return []


@pytest.mark.asyncio
async def test_highlights_use_exact_titles_and_verify_exact_selected_set() -> None:
    page = _HighlightPage(
        {
            "portfolio": [_HighlightButton("Family Law Growth")],
            "certifications": [_HighlightButton("Google Ads Search Certification")],
            "upwork_jobs": [],
        }
    )
    ok, error = await proposals._select_profile_highlights(page, ["Family Law"])
    assert ok is False
    assert error and "exact live title" in error
    assert page.options["portfolio"][0].selected is False

    ok, error = await proposals._select_profile_highlights(
        page,
        ["  FAMILY   LAW GROWTH  "],
    )
    assert (ok, error) == (True, None)
    assert page.options["portfolio"][0].selected is True


@pytest.mark.asyncio
async def test_highlights_inspect_extra_tabs_but_still_require_known_tabs() -> None:
    extra = _HighlightButton("Specialized Profile Result")
    page = _HighlightPage(
        {
            "portfolio": [_HighlightButton("Family Law Growth")],
            "certifications": [],
            "upwork_jobs": [],
            "specialized_profiles": [extra],
        }
    )

    ok, error = await proposals._select_profile_highlights(page, [])

    assert (ok, error) == (True, None)
    assert extra.selected is False

    missing = _HighlightPage(
        {
            "portfolio": [],
            "certifications": [],
            "specialized_profiles": [],
        }
    )
    ok, error = await proposals._select_profile_highlights(missing, [])
    assert ok is False
    assert error and "required profile-highlight tab set" in error
