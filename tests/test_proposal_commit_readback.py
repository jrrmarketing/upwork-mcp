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


class _UnrestorableField(_Field):
    def __init__(self, original: str) -> None:
        super().__init__()
        self.value = original
        self.original = original

    async def fill(self, value: str) -> None:
        if value == self.original and self.value != self.original:
            raise RuntimeError("persisted owner draft refused restoration")
        self.value = value


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


class _FadingSubmitButton(_Button):
    def __init__(self, callback=None) -> None:
        super().__init__("Submit proposal", callback)
        self.visible_reads = 0

    async def is_visible(self) -> bool:
        self.visible_reads += 1
        return self.visible_reads == 1


class _DispatchThenRaiseButton(_Button):
    def __init__(self) -> None:
        super().__init__("Submit proposal")
        self.dispatches = 0

    async def click(self) -> None:
        self.dispatches += 1
        raise RuntimeError("browser lost acknowledgement after dispatch")


class _FallbackTrackingPage:
    def __init__(self) -> None:
        self.evaluate_calls = 0

    async def evaluate(self, *_args, **_kwargs) -> bool:
        self.evaluate_calls += 1
        return True


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
    assert await proposals._exact_final_send_control(
        _ControlScope([visible, _Button("Send for 99 Connects")]), 12
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
        self.reload_count = 0
        self.on_atomic_submit = None
        self.atomic_submit_raise_after_dispatch = False
        self.proposal_event_generation = 0
        self.proposal_handler_generation = 0
        self.proposal_guard_callback = None
        self.proposal_guard_snapshot: tuple[Any, ...] | None = None
        self.submit_button = _Button(
            "Submit proposal",
            lambda: setattr(self, "submit_clicks", self.submit_clicks + 1),
        )

    async def goto(self, url: str, **_kwargs: Any) -> None:
        self.url = url

    async def reload(self, **_kwargs: Any) -> None:
        self.reload_count += 1
        self.rate.visible = True
        self.cover.visible = True
        self.answer.visible = True
        self.duration.visible = True
        if self.increase is not None:
            self.increase.visible = True

    def _guard_snapshot(self) -> tuple[Any, ...]:
        return (
            self.url,
            self.body,
            self.rate.value,
            self.cover.value,
            self.answer.value,
            self.duration.label,
            self.increase.label if self.increase is not None else None,
            self.submit_button.text,
        )

    async def evaluate(self, _script: str, args: dict[str, Any]) -> dict[str, Any]:
        operation = args.get("operation")
        if operation == "install_proposal_commit_guard":
            self.proposal_guard_snapshot = self._guard_snapshot()
            self.proposal_guard_callback = self.submit_button.callback
            return {
                "status": "ready",
                "generation": 0,
                "eventGeneration": 0,
                "handlerGeneration": 0,
            }
        if operation != "atomic_first_submit":
            raise AssertionError(f"Unexpected page.evaluate operation: {operation}")
        if self.on_atomic_submit is not None:
            callback = self.on_atomic_submit
            self.on_atomic_submit = None
            callback()
        if self.proposal_guard_snapshot != self._guard_snapshot():
            return {
                "status": "rejected",
                "dispatchStarted": False,
                "message": "proposal form mutated",
            }
        if self.proposal_event_generation != args["eventGeneration"]:
            return {
                "status": "rejected",
                "dispatchStarted": False,
                "message": "proposal input event generation changed",
            }
        if self.proposal_handler_generation != args["handlerGeneration"]:
            return {
                "status": "rejected",
                "dispatchStarted": False,
                "message": "Submit event handler generation changed",
            }
        self.submit_queries += 1
        if not await self.submit_button.is_visible() or not await self.submit_button.is_enabled():
            return {
                "status": "rejected",
                "dispatchStarted": False,
                "message": "Submit control not actionable",
            }
        if self.submit_button.text != "Submit proposal":
            return {
                "status": "rejected",
                "dispatchStarted": False,
                "message": "Submit control identity changed",
            }
        if self.submit_button.callback is not self.proposal_guard_callback:
            return {
                "status": "rejected",
                "dispatchStarted": False,
                "message": "Submit handler changed",
            }
        if self.submit_button.callback:
            self.submit_button.callback()
        if self.atomic_submit_raise_after_dispatch:
            raise RuntimeError("execution context failed after dispatch")
        return {"status": "clicked", "dispatchStarted": True}

    async def query_selector(self, selector: str):
        if selector == "body":
            return _Text(self.body)
        if 'data-test="job-title"' in selector:
            return _Text("Google Ads audit")
        return None

    async def query_selector_all(self, selector: str) -> list[Any]:
        if selector.startswith('button[data-test="submit-proposal"]'):
            self.submit_queries += 1
            return [self.submit_button]
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

    assert result["status"] in {"error", "live_form_mismatch", "draft_state_unavailable"}
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

    assert result["status"] == "draft_state_unavailable"
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

    assert result["status"] == "draft_state_unknown"
    assert result["preclick_failure_status"] == "live_form_mismatch"
    assert result["external_action_taken"] is True
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

    assert result["status"] == "draft_state_unavailable"
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

    assert result["status"] == "draft_state_unknown"
    assert result["preclick_failure_status"] == "live_form_mismatch"
    assert result["external_action_taken"] is True
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
async def test_preclick_failure_restores_every_field_and_owner_reload_confirms_it(
    monkeypatch,
) -> None:
    page = _CommitPage()
    page.rate.value = "51"
    page.cover.value = "Original cover draft"
    page.answer.value = "Original answer draft"
    page.duration.label = "Less than 1 month"
    assert page.increase is not None
    page.increase.label = "Quarterly"
    _commercial_inspectors(monkeypatch, changed="fee")

    result = await proposals._submit_proposal_on_page(_params(), page)

    assert result["status"] == "live_form_mismatch"
    assert result["draft_restored"] is True
    assert result["external_action_taken"] is False
    assert page.rate.value == "51"
    assert page.cover.value == "Original cover draft"
    assert page.answer.value == "Original answer draft"
    assert page.duration.label == "Less than 1 month"
    assert page.increase.label == "Quarterly"
    assert page.reload_count == 1
    restoration = result["draft_restoration_readback"]
    assert restoration["owner_reload_completed"] is True
    assert restoration["identity_confirmed"] is True
    assert restoration["snapshot_confirmed"] is True
    assert restoration["original_snapshot_sha256"] == restoration["live_snapshot_sha256"]
    assert page.submit_queries == 0
    assert page.submit_clicks == 0


@pytest.mark.asyncio
async def test_unverifiable_persisted_proposal_draft_is_terminal_unknown(monkeypatch) -> None:
    page = _CommitPage()
    page.cover = _UnrestorableField("Original cover draft")
    _commercial_inspectors(monkeypatch, changed="fee")

    result = await proposals._submit_proposal_on_page(_params(), page)

    assert result["status"] == "draft_state_unknown"
    assert result["preclick_failure_status"] == "live_form_mismatch"
    assert result["external_action_taken"] is True
    assert result["draft_restored"] is False
    assert "do not retry automatically" in result["message"]
    assert result["draft_restoration_readback"]["owner_reload_completed"] is True
    assert result["draft_restoration_readback"]["snapshot_confirmed"] is False
    assert page.cover.value == "Exact approved copy"
    assert page.submit_queries == 0
    assert page.submit_clicks == 0


@pytest.mark.asyncio
async def test_one_restore_failure_does_not_skip_other_touched_fields(monkeypatch) -> None:
    page = _CommitPage()
    page.rate = _UnrestorableField("51")
    page.cover.value = "Original cover"
    page.answer.value = "Original answer"
    page.duration.label = "Less than 1 month"
    assert page.increase is not None
    page.increase.label = "Quarterly"
    _commercial_inspectors(monkeypatch, changed="fee")

    result = await proposals._submit_proposal_on_page(_params(), page)

    assert result["status"] == "draft_state_unknown"
    assert result["external_action_taken"] is True
    assert result["draft_restoration_readback"]["local_restoration"]["price"] is False
    assert result["draft_restoration_readback"]["local_restoration"]["cover"] is True
    assert result["draft_restoration_readback"]["local_restoration"]["answers"] is True
    assert result["draft_restoration_readback"]["local_restoration"]["duration"] is True
    assert result["draft_restoration_readback"]["local_restoration"]["rate_increase"] is True
    assert page.cover.value == "Original cover"
    assert page.answer.value == "Original answer"
    assert page.duration.label == "Less than 1 month"
    assert page.increase.label == "Quarterly"


@pytest.mark.asyncio
async def test_unexpected_preclick_exception_still_restores_and_reload_proves_draft(
    monkeypatch,
) -> None:
    page = _CommitPage()
    page.rate.value = "57"
    page.cover.value = "Original cover"
    page.answer.value = "Original answer"
    _commercial_inspectors(monkeypatch)

    async def raise_during_duration(_page, _duration):
        raise RuntimeError("detached duration control")

    monkeypatch.setattr(proposals, "_select_duration", raise_during_duration)
    result = await proposals._submit_proposal_on_page(_params(), page)

    assert result["status"] == "error"
    assert result["draft_restored"] is True
    assert result["external_action_taken"] is False
    assert page.rate.value == "57"
    assert page.cover.value == "Original cover"
    assert page.answer.value == "Original answer"
    assert page.reload_count == 1
    assert page.submit_queries == 0


@pytest.mark.asyncio
async def test_first_submit_actionability_failure_restores_entire_draft(monkeypatch) -> None:
    page = _CommitPage()
    page.rate.value = "58"
    page.cover.value = "Original cover"
    page.answer.value = "Original answer"
    page.submit_button = _FadingSubmitButton(
        lambda: setattr(page, "submit_clicks", page.submit_clicks + 1)
    )
    _commercial_inspectors(monkeypatch)

    result = await proposals._submit_proposal_on_page(_params(), page)

    assert result["status"] == "submit_control_changed"
    assert result["draft_restored"] is True
    assert result["external_action_taken"] is False
    assert page.rate.value == "58"
    assert page.cover.value == "Original cover"
    assert page.answer.value == "Original answer"
    assert page.reload_count == 1
    assert page.submit_clicks == 0


@pytest.mark.asyncio
async def test_atomic_submit_mutation_is_rejected_and_entire_draft_restored(
    monkeypatch,
) -> None:
    page = _CommitPage()
    page.rate.value = "58"
    page.cover.value = "Original cover"
    page.answer.value = "Original answer"
    wrong_clicks = 0

    def mutate_submit() -> None:
        page.submit_button.text = "Delete"

        def wrong_action() -> None:
            nonlocal wrong_clicks
            wrong_clicks += 1

        page.submit_button.callback = wrong_action

    page.on_atomic_submit = mutate_submit
    _commercial_inspectors(monkeypatch)

    result = await proposals._submit_proposal_on_page(_params(), page)

    assert result["status"] == "submit_control_changed"
    assert result["draft_restored"] is True
    assert result["external_action_taken"] is False
    assert page.rate.value == "58"
    assert page.cover.value == "Original cover"
    assert page.answer.value == "Original answer"
    assert page.reload_count == 1
    assert page.submit_clicks == 0
    assert wrong_clicks == 0


@pytest.mark.asyncio
async def test_programmatic_form_value_race_is_rejected_by_commit_fingerprint(
    monkeypatch,
) -> None:
    page = _CommitPage()
    page.cover.value = "Original cover"
    page.on_atomic_submit = lambda: setattr(page.cover, "value", "Programmatic reset")
    _commercial_inspectors(monkeypatch)

    result = await proposals._submit_proposal_on_page(_params(), page)

    assert result["status"] == "submit_control_changed"
    assert result["draft_restored"] is True
    assert result["external_action_taken"] is False
    assert page.cover.value == "Original cover"
    assert page.submit_clicks == 0


@pytest.mark.asyncio
async def test_proposal_input_event_aba_is_rejected_with_approved_dom_restored(
    monkeypatch,
) -> None:
    page = _CommitPage()

    def event_aba() -> None:
        page.cover.value = "UNAPPROVED COVER"
        page.proposal_event_generation += 1
        page.cover.value = "Exact approved copy"

    page.on_atomic_submit = event_aba
    _commercial_inspectors(monkeypatch)

    result = await proposals._submit_proposal_on_page(_params(), page)

    assert result["status"] == "submit_control_changed"
    assert result["external_action_taken"] is False
    assert result["draft_restored"] is True
    assert page.submit_clicks == 0


@pytest.mark.asyncio
async def test_submit_handler_replacement_is_rejected_without_wrong_dispatch(monkeypatch) -> None:
    page = _CommitPage()
    wrong_clicks = 0

    def replace_handler() -> None:
        def wrong_action() -> None:
            nonlocal wrong_clicks
            wrong_clicks += 1

        page.submit_button.callback = wrong_action

    page.on_atomic_submit = replace_handler
    _commercial_inspectors(monkeypatch)

    result = await proposals._submit_proposal_on_page(_params(), page)

    assert result["status"] == "submit_control_changed"
    assert result["external_action_taken"] is False
    assert page.submit_clicks == 0
    assert wrong_clicks == 0


@pytest.mark.asyncio
async def test_submit_event_listener_replacement_is_rejected_without_wrong_dispatch(
    monkeypatch,
) -> None:
    page = _CommitPage()
    wrong_clicks = 0

    def replace_listener() -> None:
        nonlocal wrong_clicks
        page.proposal_handler_generation += 2

        def wrong_action() -> None:
            nonlocal wrong_clicks
            wrong_clicks += 1

        page.submit_button.callback = wrong_action

    page.on_atomic_submit = replace_listener
    _commercial_inspectors(monkeypatch)

    result = await proposals._submit_proposal_on_page(_params(), page)

    assert result["status"] == "submit_control_changed"
    assert result["external_action_taken"] is False
    assert page.submit_clicks == 0
    assert wrong_clicks == 0


@pytest.mark.asyncio
async def test_atomic_submit_evaluation_failure_after_dispatch_is_terminal_unknown(
    monkeypatch,
) -> None:
    page = _CommitPage()
    page.atomic_submit_raise_after_dispatch = True
    _commercial_inspectors(monkeypatch)

    async def unconfirmed(*_args, **_kwargs):
        return {"confirmed": False}

    monkeypatch.setattr(proposals, "_proposal_confirmation", unconfirmed)
    result = await proposals._submit_proposal_on_page(_params(), page)

    assert result["status"] == "unknown"
    assert result["external_action_taken"] is True
    assert result["boost_spend_verified"] is False
    assert page.submit_clicks == 1
    assert page.reload_count == 0


@pytest.mark.asyncio
async def test_native_click_that_dispatches_then_raises_is_never_retried() -> None:
    page = _FallbackTrackingPage()
    button = _DispatchThenRaiseButton()

    with pytest.raises(proposals._ClickOutcomeUnknown):
        await proposals._click(page, button)

    assert button.dispatches == 1
    assert page.evaluate_calls == 0


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
    assert result["status"] == "draft_state_unavailable"
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
    def __init__(self, *, readable: bool = True, visible: bool = True) -> None:
        super().__init__("Don't boost")
        self.selected = False
        self.readable = readable
        self.visible = visible

    async def is_visible(self) -> bool:
        return self.visible

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
        extra_send_labels: list[str] | None = None,
    ) -> None:
        super().__init__("Boost your proposal with Connects")
        self.boost = _Field(wrong_readback=boost_readback)
        self.choice = choice
        self.positive_selected = False
        self.send_queries = 0
        self.send = _Button(send_label)
        self.extra_sends = [_Button(label) for label in (extra_send_labels or [])]

    async def is_visible(self) -> bool:
        return True

    async def query_selector_all(self, selector: str) -> list[Any]:
        if 'input[name*="boost"]' in selector:
            return [self.boost]
        if "Don't boost" in selector or "No, thanks" in selector:
            return [self.choice] if self.choice else []
        if selector == "button":
            self.send_queries += 1
            return [self.send, *self.extra_sends]
        return []


class _BoostPage:
    def __init__(self, dialog: _BoostDialog) -> None:
        self.dialog = dialog
        self.on_atomic_final_send = None
        self.raise_after_final_send = False
        self.final_send_event_generation = 0
        self.final_send_handler_generation = 0
        self.final_send_guard_snapshot: tuple[Any, ...] | None = None
        self.final_send_guard_callback = None

    async def query_selector_all(self, selector: str) -> list[Any]:
        return [self.dialog] if selector == '[role="dialog"]' else []

    async def evaluate(self, _script: str, args: dict[str, Any]) -> dict[str, Any]:
        operation = args.get("operation")
        if operation == "install_final_send_commit_guard":
            self.final_send_guard_snapshot = (
                self.dialog.choice.selected if self.dialog.choice else None,
                self.dialog.positive_selected,
                self.dialog.send.text,
                tuple(item.text for item in self.dialog.extra_sends),
            )
            self.final_send_guard_callback = self.dialog.send.callback
            return {
                "status": "ready",
                "generation": 0,
                "eventGeneration": 0,
                "handlerGeneration": 0,
            }
        if operation != "atomic_final_send":
            raise AssertionError("Unexpected atomic operation")
        if self.on_atomic_final_send is not None:
            callback = self.on_atomic_final_send
            self.on_atomic_final_send = None
            callback()
        current_snapshot = (
            self.dialog.choice.selected if self.dialog.choice else None,
            self.dialog.positive_selected,
            self.dialog.send.text,
            tuple(item.text for item in self.dialog.extra_sends),
        )
        if (
            current_snapshot != self.final_send_guard_snapshot
            or self.final_send_event_generation != args["eventGeneration"]
            or self.final_send_handler_generation != args["handlerGeneration"]
        ):
            return {
                "status": "rejected",
                "dispatchStarted": False,
                "message": "final boost dialog changed",
            }
        if (
            self.dialog.choice is None
            or not self.dialog.choice.selected
            or self.dialog.positive_selected
        ):
            return {
                "status": "rejected",
                "dispatchStarted": False,
                "message": "no-boost state changed",
            }
        send_labels = [self.dialog.send.text, *(item.text for item in self.dialog.extra_sends)]
        if send_labels != [f'Send for {args["baseConnects"]} Connects']:
            return {
                "status": "rejected",
                "dispatchStarted": False,
                "message": "approved cost changed",
            }
        if self.dialog.send.callback is not self.final_send_guard_callback:
            return {
                "status": "rejected",
                "dispatchStarted": False,
                "message": "final Send handler changed",
            }
        if self.dialog.send.callback:
            self.dialog.send.callback()
        if self.raise_after_final_send:
            raise RuntimeError("execution context failed after dispatch")
        return {"status": "clicked", "dispatchStarted": True}


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
    guard, error = await proposals._configure_boost_step(_BoostPage(unboosted), 0, 12)
    assert guard is not None and error is None
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
async def test_hidden_enabled_no_boost_control_never_reaches_final_send() -> None:
    hidden_choice = _Choice(visible=False)
    dialog = _BoostDialog(choice=hidden_choice)

    send, error = await proposals._configure_boost_step(_BoostPage(dialog), 0, 12)

    assert send is None
    assert error and "no-boost control" in error
    assert hidden_choice.selected is False
    assert dialog.send_queries == 0


@pytest.mark.asyncio
async def test_atomic_final_send_revalidates_no_boost_and_cost_before_one_dispatch() -> None:
    clicks = 0

    def sent() -> None:
        nonlocal clicks
        clicks += 1

    dialog = _BoostDialog(choice=_Choice())
    dialog.send.callback = sent
    page = _BoostPage(dialog)
    guard, error = await proposals._configure_boost_step(page, 0, 12)
    assert guard is not None and error is None

    status, click_error = await proposals._atomic_click_final_send(page, _params(), guard)

    assert (status, click_error) == ("clicked", None)
    assert clicks == 1


@pytest.mark.asyncio
async def test_atomic_final_send_rejects_positive_boost_race_without_dispatch() -> None:
    clicks = 0

    def sent() -> None:
        nonlocal clicks
        clicks += 1

    dialog = _BoostDialog(choice=_Choice())
    dialog.send.callback = sent
    page = _BoostPage(dialog)
    guard, error = await proposals._configure_boost_step(page, 0, 12)
    assert guard is not None and error is None
    page.on_atomic_final_send = lambda: setattr(dialog, "positive_selected", True)

    status, click_error = await proposals._atomic_click_final_send(page, _params(), guard)

    assert status == "rejected"
    assert click_error and "dialog" in click_error
    assert clicks == 0


@pytest.mark.asyncio
async def test_final_boost_event_aba_is_rejected_with_no_boost_dom_restored() -> None:
    clicks = 0

    def sent() -> None:
        nonlocal clicks
        clicks += 1

    dialog = _BoostDialog(choice=_Choice())
    dialog.send.callback = sent
    page = _BoostPage(dialog)
    guard, error = await proposals._configure_boost_step(page, 0, 12)
    assert guard is not None and error is None

    def boost_event_aba() -> None:
        assert dialog.choice is not None
        dialog.choice.selected = False
        dialog.positive_selected = True
        page.final_send_event_generation += 1
        dialog.choice.selected = True
        dialog.positive_selected = False

    page.on_atomic_final_send = boost_event_aba

    status, click_error = await proposals._atomic_click_final_send(page, _params(), guard)

    assert status == "rejected"
    assert click_error and "dialog" in click_error
    assert clicks == 0


@pytest.mark.asyncio
async def test_final_send_handler_replacement_is_rejected_without_wrong_dispatch() -> None:
    clicks = 0
    wrong_clicks = 0

    def sent() -> None:
        nonlocal clicks
        clicks += 1

    dialog = _BoostDialog(choice=_Choice())
    dialog.send.callback = sent
    page = _BoostPage(dialog)
    guard, error = await proposals._configure_boost_step(page, 0, 12)
    assert guard is not None and error is None

    def replace_handler() -> None:
        def wrong_action() -> None:
            nonlocal wrong_clicks
            wrong_clicks += 1

        dialog.send.callback = wrong_action

    page.on_atomic_final_send = replace_handler
    status, click_error = await proposals._atomic_click_final_send(page, _params(), guard)

    assert status == "rejected"
    assert click_error and "handler" in click_error
    assert clicks == 0
    assert wrong_clicks == 0


@pytest.mark.asyncio
async def test_final_send_listener_replacement_is_rejected_without_wrong_dispatch() -> None:
    clicks = 0
    wrong_clicks = 0

    def sent() -> None:
        nonlocal clicks
        clicks += 1

    dialog = _BoostDialog(choice=_Choice())
    dialog.send.callback = sent
    page = _BoostPage(dialog)
    guard, error = await proposals._configure_boost_step(page, 0, 12)
    assert guard is not None and error is None

    def replace_listener() -> None:
        nonlocal wrong_clicks
        page.final_send_handler_generation += 2

        def wrong_action() -> None:
            nonlocal wrong_clicks
            wrong_clicks += 1

        dialog.send.callback = wrong_action

    page.on_atomic_final_send = replace_listener
    status, click_error = await proposals._atomic_click_final_send(page, _params(), guard)

    assert status == "rejected"
    assert click_error and "dialog" in click_error
    assert clicks == 0
    assert wrong_clicks == 0


@pytest.mark.asyncio
async def test_atomic_final_send_rejects_second_wrong_cost_send_without_dispatch() -> None:
    clicks = 0

    def sent() -> None:
        nonlocal clicks
        clicks += 1

    dialog = _BoostDialog(choice=_Choice())
    dialog.send.callback = sent
    page = _BoostPage(dialog)
    guard, error = await proposals._configure_boost_step(page, 0, 12)
    assert guard is not None and error is None
    page.on_atomic_final_send = lambda: dialog.extra_sends.append(
        _Button("Send for 99 Connects")
    )

    status, click_error = await proposals._atomic_click_final_send(page, _params(), guard)

    assert status == "rejected"
    assert click_error and "dialog" in click_error
    assert clicks == 0


@pytest.mark.asyncio
async def test_atomic_final_send_evaluation_failure_is_unknown_after_one_dispatch() -> None:
    clicks = 0

    def sent() -> None:
        nonlocal clicks
        clicks += 1

    dialog = _BoostDialog(choice=_Choice())
    dialog.send.callback = sent
    page = _BoostPage(dialog)
    guard, error = await proposals._configure_boost_step(page, 0, 12)
    assert guard is not None and error is None
    page.raise_after_final_send = True

    status, click_error = await proposals._atomic_click_final_send(page, _params(), guard)

    assert status == "unknown"
    assert click_error and "unknown" in click_error
    assert clicks == 1


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
        self.selected = not self.selected
        self.text = "Selected" if self.selected else "Select highlight"

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
            return [self.page.save_button]
        return []


class _Keyboard:
    async def press(self, _key: str) -> None:
        return None


class _HighlightPage:
    def __init__(
        self,
        options: dict[str, list[_HighlightButton]],
        *,
        opener_visible: bool = True,
        save_visible: bool = True,
    ) -> None:
        self.options = options
        self.tab = next(iter(options))
        self.open = False
        self.opener_clicks = 0
        self.save_clicks = 0
        self.keyboard = _Keyboard()
        self.chooser = _HighlightChooser(self)

        def open_chooser() -> None:
            self.opener_clicks += 1
            self.open = True

        def save_chooser() -> None:
            self.save_clicks += 1
            self.open = False

        opener_type = _Button if opener_visible else _HiddenButton
        save_type = _Button if save_visible else _HiddenButton
        self.opener = opener_type("Add a portfolio project", open_chooser)
        self.save_button = save_type("Add to highlights", save_chooser)

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
                return [self.opener]
        if '[role="dialog"]:has-text("Add profile highlights")' in selector:
            return [self.chooser] if self.open else []
        if "aria-label" in selector and "Close" in selector and self.open:
            return [_Button("Close", lambda: setattr(self, "open", False))]
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
async def test_profile_highlight_snapshot_restores_original_selected_set() -> None:
    selected = _HighlightButton("Family Law Growth")
    page = _HighlightPage(
        {
            "portfolio": [selected],
            "certifications": [],
            "upwork_jobs": [],
        }
    )
    snapshot, error = await proposals._capture_profile_highlight_state(page)
    assert error is None
    assert snapshot is not None
    assert snapshot["selected"] == []

    assert await proposals._select_profile_highlights(page, ["Family Law Growth"]) == (True, None)
    assert selected.selected is True
    assert await proposals._restore_profile_highlight_state(page, snapshot) is True
    assert selected.selected is False
    assert page.save_clicks == 2


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


@pytest.mark.asyncio
async def test_hidden_enabled_profile_highlight_opener_is_never_clicked() -> None:
    page = _HighlightPage(
        {"portfolio": [], "certifications": [], "upwork_jobs": []},
        opener_visible=False,
    )

    chooser = await proposals._open_profile_highlight_chooser(page)

    assert chooser is None
    assert page.opener_clicks == 0
    assert page.open is False


@pytest.mark.asyncio
async def test_hidden_enabled_profile_highlight_save_is_never_clicked() -> None:
    page = _HighlightPage(
        {
            "portfolio": [_HighlightButton("Family Law Growth")],
            "certifications": [],
            "upwork_jobs": [],
        },
        save_visible=False,
    )

    ok, error = await proposals._select_profile_highlights(page, ["Family Law Growth"])

    assert ok is False
    assert error and "Add to highlights control" in error
    assert page.save_clicks == 0
