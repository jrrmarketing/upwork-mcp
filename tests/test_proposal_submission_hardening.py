"""Regression tests for exact-target proposal submission."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest
from pydantic import ValidationError

from upwork_mcp.prepared_actions import approve_action, prepare_action
from upwork_mcp.tools import management, proposals


@pytest.mark.parametrize(
    ("value", "canonical", "job_id", "kind"),
    [
        (
            "https://www.upwork.com/jobs/~abc123/?source=search#ignored",
            "https://www.upwork.com/jobs/~abc123",
            "~abc123",
            "job",
        ),
        (
            "https://www.upwork.com/nx/proposals/job/~abc123/apply/?source=job",
            "https://www.upwork.com/nx/proposals/job/~abc123/apply",
            "~abc123",
            "application",
        ),
        (
            "https://www.upwork.com/ab/proposals/job/~abc123/apply/",
            "https://www.upwork.com/nx/proposals/job/~abc123/apply",
            "~abc123",
            "application",
        ),
    ],
)
def test_exact_job_and_application_routes_are_canonicalized(
    value: str,
    canonical: str,
    job_id: str,
    kind: str,
) -> None:
    assert proposals.parse_job_or_application_url(value) == (canonical, job_id, kind)


@pytest.mark.parametrize(
    "value",
    [
        "https://www.upwork.com/jobs/",
        "https://www.upwork.com/jobs/not-an-item",
        "https://www.upwork.com/jobs/~abc123/edit",
        "https://www.upwork.com/nx/proposals/job/~abc123",
        "https://www.upwork.com/nx/proposals/job/~abc123/apply/extra",
        "https://www.upwork.com/nx/proposals/",
        "https://www.upwork.com/nx/proposals/interview/uid/3333333333333333333/accept",
        "https://www.upwork.com/jobs/?next=/nx/proposals/job/~abc123/apply",
        "https://www.upwork.com:444/jobs/~abc123",
    ],
)
def test_proposal_preflight_rejects_non_item_and_query_hidden_routes(value: str) -> None:
    with pytest.raises(ValueError):
        proposals.parse_job_or_application_url(value)


def _proposal_params(**updates: Any) -> proposals.SubmitProposalParams:
    values: dict[str, Any] = {
        "job_url": "https://www.upwork.com/jobs/~abc123",
        "job_id": "~abc123",
        "form_url": "https://www.upwork.com/nx/proposals/job/~abc123/apply",
        "job_title": "Google Ads audit",
        "job_type": "hourly",
        "cover_letter": "Exact approved copy",
        "fee_net_text": ["Upwork service fee $6.30", "You'll receive $56.70 net"],
        "fee_net_status": "complete",
        "fee_net_price_amount": "63.00",
        "fee_net_source": "scoped_reversible_price_preflight",
        "boost_auction_text": [],
        "boost_auction_status": "unavailable",
        "rate": 63,
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
    if "fee_net_price_amount" not in updates:
        price = values["rate"] if values["rate"] is not None else values["bid"]
        values["fee_net_price_amount"] = f"{price:.2f}"
    if values["job_type"] == "fixed" and "rate_increase_control_status" not in updates:
        values["rate_increase_control_status"] = "not_applicable"
    return proposals.SubmitProposalParams(**values)


def test_submission_schema_binds_route_identity_and_fixed_payment_terms() -> None:
    with pytest.raises(ValidationError, match="job_id must match"):
        _proposal_params(job_id="~different")

    with pytest.raises(ValidationError, match="payment_structure"):
        _proposal_params(job_type="fixed", rate=None, bid=500)

    with pytest.raises(ValidationError, match="Hourly proposals cannot"):
        _proposal_params(payment_structure="by_project")

    with pytest.raises(ValidationError, match="add up exactly"):
        _proposal_params(
            job_type="fixed",
            rate=None,
            bid=500,
            payment_structure="by_milestone",
            milestones=[
                {"description": "Audit", "due_date": "2026-09-01", "amount": 499},
            ],
        )

    with pytest.raises(ValidationError, match="greater than or equal to 50"):
        _proposal_params(rate=49)


def test_identity_and_payment_structure_are_approval_bound() -> None:
    original = _proposal_params()
    changed_title = _proposal_params(job_title="Different Google Ads audit")
    assert proposals.approval_payload_digest(
        proposals.proposal_submission_payload(original)
    ) != proposals.approval_payload_digest(
        proposals.proposal_submission_payload(changed_title)
    )
    for changed in (
        _proposal_params(
            fee_net_text=["Upwork service fee $7.00", "You'll receive $56.00 net"]
        ),
        _proposal_params(boost_auction_text=["Boost your proposal"], boost_auction_status="incomplete"),
    ):
        assert proposals.approval_payload_digest(
            proposals.proposal_submission_payload(original)
        ) != proposals.approval_payload_digest(
            proposals.proposal_submission_payload(changed)
        )

    with pytest.raises(ValidationError, match="Hourly proposals require"):
        _proposal_params(rate_increase_control_status="not_applicable")

    fixed = _proposal_params(
        job_type="fixed",
        rate=None,
        bid=500,
        payment_structure="by_project",
        rate_increase_control_status="not_applicable",
    )
    payload = proposals.proposal_submission_payload(fixed)
    assert payload["job_id"] == "~abc123"
    assert payload["form_url"].endswith("/~abc123/apply")
    assert payload["job_title"] == "Google Ads audit"
    assert payload["job_type"] == "fixed"
    assert payload["payment_structure"] == "by_project"
    assert payload["milestones"] == []
    assert payload["fee_net_status"] == "complete"
    assert payload["boost_auction_status"] == "unavailable"
    assert payload["screening_questions_status"] == "complete"
    assert payload["duration_options_status"] == "complete"
    assert payload["available_profile_highlights_status"] == "complete"
    assert payload["rate_increase_control_status"] == "not_applicable"


def test_submission_schema_requires_complete_discovery_and_auction_for_boost() -> None:
    with pytest.raises(ValidationError, match="screening-question"):
        _proposal_params(screening_questions_status="incomplete")
    with pytest.raises(ValidationError, match="fee/net"):
        _proposal_params(fee_net_status="unavailable", fee_net_text=[])
    with pytest.raises(ValidationError, match="base Connects"):
        _proposal_params(base_connects_status="unavailable", base_connects=None)
    with pytest.raises(ValidationError, match="fee_net_price_amount"):
        _proposal_params(fee_net_price_amount="64.00")
    with pytest.raises(ValidationError, match="positive boost"):
        _proposal_params(boost_connects=5)
    with pytest.raises(ValidationError, match="positive boost"):
        _proposal_params(
            boost_connects=5,
            boost_auction_text=["Boost auction top bid 8 Connects"],
            boost_auction_status="complete",
        )


def test_submission_schema_rejects_blank_duplicate_or_multirow_prepare_drift() -> None:
    with pytest.raises(ValidationError, match="blank"):
        _proposal_params(answers=["  "])
    with pytest.raises(ValidationError, match="blank"):
        _proposal_params(profile_highlights=["  "])
    with pytest.raises(ValidationError, match="duplicates"):
        _proposal_params(
            profile_highlights=[
                "Google Ads Search Certification",
                " google ads search certification ",
            ]
        )
    with pytest.raises(ValidationError, match="at most 1 item"):
        _proposal_params(
            job_type="fixed",
            rate=None,
            bid=500,
            payment_structure="by_milestone",
            milestones=[
                {"description": "Audit", "due_date": "2026-09-01", "amount": 250},
                {"description": "Review", "due_date": "2026-09-08", "amount": 250},
            ],
        )


def test_job_type_detection_prefers_form_structure_and_fails_closed_on_ambiguous_text() -> None:
    assert proposals._detect_job_type("By project\nProfile rate $63/hr") == "fixed"
    assert proposals._detect_job_type("Rate increase frequency\nFixed-price work in description") == "hourly"
    assert proposals._detect_job_type("Hourly contract described as fixed-price") is None


def _job() -> dict[str, Any]:
    return {
        "url": "https://www.upwork.com/jobs/~abc123",
        "title": "Google Ads audit",
        "description": "Paid search lead quality and call tracking",
        "job_type": "hourly",
        "hourly_rate_min": 50,
        "hourly_rate_max": 90,
        "proposal_count": 5,
        "connects_required": 8,
        "client": {
            "payment_verified": True,
            "total_spent": 50_000,
            "total_hires": 10,
            "hire_rate": 80,
            "rating": 4.9,
            "avg_hourly_rate_paid": 70,
        },
    }


def _form(**updates: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "job_url": "https://www.upwork.com/jobs/~abc123",
        "job_id": "~abc123",
        "form_url": "https://www.upwork.com/nx/proposals/job/~abc123/apply",
        "job_title": "Google Ads audit",
        "job_type": "hourly",
        "fixed_payment_structures": [],
        "form_status": "ready",
        "existing_proposal": False,
        "screening_questions": [],
        "screening_questions_status": "complete",
        "duration_options": [
            "Less than 1 month",
            "1 to 3 months",
            "3 to 6 months",
            "More than 6 months",
        ],
        "duration_options_status": "complete",
        "base_connects": 8,
        "base_connects_status": "complete",
        "fee_net_text": ["Upwork service fee $6.30", "You'll receive $56.70 net"],
        "fee_net_status": "complete",
        "fee_net_price_amount": "63.00",
        "fee_net_source": "scoped_reversible_price_preflight",
        "boost_auction_text": [],
        "boost_auction_status": "unavailable",
        "available_profile_highlights": ["Google Ads Search Certification"],
        "available_profile_highlights_status": "complete",
        "rate_increase_control_status": "complete",
    }
    values.update(updates)
    return values


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "form_update",
    [
        {
            "job_id": "~different",
            "form_url": "https://www.upwork.com/nx/proposals/job/~different/apply",
        },
        {"job_title": "Different Google Ads job"},
        {"job_type": "fixed", "fixed_payment_structures": ["by_project"]},
    ],
)
async def test_preparation_fails_closed_on_live_job_form_identity_mismatch(
    monkeypatch,
    tmp_path,
    form_update: dict[str, Any],
) -> None:
    async def fake_job(_params):
        return _job()

    async def fake_form(_params):
        return _form(**form_update)

    monkeypatch.setenv("UPWORK_MCP_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(management, "get_job_details", fake_job)
    monkeypatch.setattr(management, "inspect_proposal_form", fake_form)
    result = await management.prepare_proposal(
        management.PrepareProposalParams(
            job_url="https://www.upwork.com/jobs/~abc123",
            cover_letter="Hey, more than happy to look properly.\n\nI can review the account.",
            rate=63,
            duration="1 to 3 months",
            profile_highlights=["Google Ads Search Certification"],
        )
    )
    assert result["ready_for_owner_approval"] is False
    assert result["prepared_action"] is None
    assert any("match" in error for error in result["errors"])


@pytest.mark.asyncio
async def test_preparation_binds_explicit_live_by_project_structure(
    monkeypatch,
    tmp_path,
) -> None:
    async def fake_job(_params):
        job = _job()
        job.update({"job_type": "fixed", "budget_min": 500, "budget_max": 500})
        job.pop("hourly_rate_min")
        job.pop("hourly_rate_max")
        return job

    async def fake_form(_params):
        return _form(
            job_type="fixed",
            fixed_payment_structures=["by_project", "by_milestone"],
            rate_increase_control_status="not_applicable",
        )

    async def fake_commercial(_params):
        return {
            "job_url": "https://www.upwork.com/jobs/~abc123",
            "job_id": "~abc123",
            "form_url": "https://www.upwork.com/nx/proposals/job/~abc123/apply",
            "job_title": "Google Ads audit",
            "job_type": "fixed",
            "form_status": "ready",
            "existing_proposal": False,
            "fee_net_text": [
                "Upwork service fee $50.00",
                "You'll receive $450.00 net",
            ],
            "fee_net_status": "complete",
            "fee_net_price_amount": "500.00",
            "fee_net_source": "scoped_reversible_price_preflight",
            "fee_net_details": {"price_restored": True},
            "price_restored": True,
            "identity_restored": True,
            "reversible_form_interaction": True,
            "external_action_taken": False,
        }

    monkeypatch.setenv("UPWORK_MCP_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(management, "get_job_details", fake_job)
    monkeypatch.setattr(management, "inspect_proposal_form", fake_form)
    monkeypatch.setattr(
        management,
        "inspect_proposal_commercial_preflight",
        fake_commercial,
    )
    result = await management.prepare_proposal(
        management.PrepareProposalParams(
            job_url="https://www.upwork.com/jobs/~abc123",
            cover_letter="Hey, more than happy to look properly.\n\nI can review the account.",
            bid=500,
            payment_structure="by_project",
            duration="1 to 3 months",
            profile_highlights=["Google Ads Search Certification"],
        )
    )
    assert result["ready_for_owner_approval"] is True
    assert result["exact_submission"]["job_type"] == "fixed"
    assert result["exact_submission"]["payment_structure"] == "by_project"
    assert result["exact_submission"]["milestones"] == []
    assert result["prepared_action"]["approved"] is False


class _Text:
    def __init__(self, text: str) -> None:
        self.text = text

    async def text_content(self) -> str:
        return self.text


class _IdentityPage:
    def __init__(self, *, live_url: str, title: str) -> None:
        self.url = live_url
        self.live_url = live_url
        self.title = title
        self.action_controls_queried = 0

    async def goto(self, _url: str, **_kwargs: Any) -> None:
        self.url = self.live_url

    async def query_selector(self, selector: str):
        if selector == "body":
            return _Text("Hourly contract\n12 Connects required to submit")
        if '[data-test="job-title"]' in selector:
            return _Text(self.title)
        if _is_action_selector(selector):
            self.action_controls_queried += 1
        return None

    async def query_selector_all(self, selector: str) -> list[Any]:
        if _is_action_selector(selector):
            self.action_controls_queried += 1
        return []


def _is_action_selector(selector: str) -> bool:
    return any(
        token in selector.casefold()
        for token in (
            "hourly-rate",
            "bid-input",
            "cover-letter",
            "screening-question",
            "duration",
            "profile-highlight",
            "submit-proposal",
            "boost",
            "send-proposal",
            "acknowledg",
            "milestone",
        )
    )


class _Browser:
    def __init__(self, page: Any) -> None:
        self.page = page

    async def ensure_logged_in(self) -> None:
        return None

    @asynccontextmanager
    async def operation(self):
        yield self.page


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("live_url", "live_title"),
    [
        (
            "https://www.upwork.com/nx/proposals/job/~abc123/apply",
            "Different Google Ads audit",
        ),
        (
            "https://www.upwork.com/nx/proposals/job/~different/apply",
            "Approved Google Ads audit",
        ),
        (
            "https://www.upwork.com/nx/proposals/1111111111111111111",
            "Approved Google Ads audit",
        ),
    ],
)
async def test_commit_rechecks_identity_before_any_submit_control_query(
    monkeypatch,
    tmp_path,
    live_url: str,
    live_title: str,
) -> None:
    params = _proposal_params(job_title="Approved Google Ads audit")
    payload = proposals.proposal_submission_payload(params)
    monkeypatch.setenv("UPWORK_MCP_STATE_DIR", str(tmp_path))
    prepared = prepare_action("proposal", payload)
    approve_action(
        prepared["action_id"],
        prepared["approval_sha256"],
        owner_approval_reference="fresh exact approval",
    )
    params = params.model_copy(update={"action_id": prepared["action_id"]})
    page = _IdentityPage(
        live_url=live_url,
        title=live_title,
    )
    monkeypatch.setattr(proposals, "get_browser", lambda: _Browser(page))

    result = await proposals.submit_proposal(params)
    assert result["status"] == "live_identity_mismatch"
    assert result["external_action_taken"] is False
    assert page.action_controls_queried == 0

    replay = await proposals.submit_proposal(params)
    assert replay["status"] == "approval_required"
    assert "already been claimed" in replay["message"]


def test_submission_schema_rejects_legacy_digest_only_authorization() -> None:
    values = _proposal_params().model_dump(exclude={"action_id"})
    values.update({"approved": True, "approval_sha256": "0" * 64})
    with pytest.raises(ValidationError):
        proposals.SubmitProposalParams(**values)


class _Radio:
    def __init__(self, *, changes: bool = True) -> None:
        self.checked = False
        self.changes = changes

    async def is_checked(self) -> bool:
        return self.checked

    async def click(self) -> None:
        if self.changes:
            self.checked = True
            if hasattr(self, "opposite"):
                self.opposite.checked = False

    async def get_attribute(self, _name: str) -> None:
        return None


class _AmountInput:
    def __init__(self) -> None:
        self.value = ""

    async def is_enabled(self) -> bool:
        return True

    async def fill(self, value: str) -> None:
        self.value = value

    async def input_value(self) -> str:
        return self.value


class _PaymentSection:
    def __init__(
        self,
        *,
        radio_changes: bool = True,
        amount_inputs: int = 1,
        rows: list[Any] | None = None,
    ) -> None:
        self.project_radio = _Radio(changes=radio_changes)
        self.milestone_radio = _Radio()
        self.project_radio.opposite = self.milestone_radio
        self.milestone_radio.opposite = self.project_radio
        self.amounts = [_AmountInput() for _ in range(amount_inputs)]
        self.rows = rows or []
        self.amount_queries = 0
        self.selectors: list[str] = []

    async def text_content(self) -> str:
        return "Payment terms By milestone By project"

    async def query_selector_all(self, selector: str) -> list[Any]:
        self.selectors.append(selector)
        if 'type="radio"' in selector and "project" in selector.casefold():
            return [self.project_radio]
        if 'type="radio"' in selector and "milestone" in selector.casefold():
            return [self.milestone_radio]
        if "bid-input" in selector:
            self.amount_queries += 1
            return self.amounts
        if "milestone-row" in selector or "milestone-item" in selector:
            return self.rows
        return []


class _PaymentPage:
    def __init__(self, *, radio_changes: bool = True, amount_inputs: int = 1) -> None:
        self.section = _PaymentSection(
            radio_changes=radio_changes,
            amount_inputs=amount_inputs,
        )
        self.selectors: list[str] = []

    @property
    def radio(self) -> _Radio:
        return self.section.project_radio

    @property
    def amounts(self) -> list[_AmountInput]:
        return self.section.amounts

    @property
    def amount_queries(self) -> int:
        return self.section.amount_queries

    async def query_selector_all(self, selector: str) -> list[Any]:
        self.selectors.append(selector)
        return [self.section] if "payment-terms" in selector else []


@pytest.mark.asyncio
async def test_by_project_is_explicitly_selected_and_value_read_back() -> None:
    page = _PaymentPage()
    params = _proposal_params(
        job_type="fixed",
        rate=None,
        bid=500,
        payment_structure="by_project",
    )
    ok, error = await proposals._configure_fixed_payment_terms(page, params)
    assert (ok, error) == (True, None)
    assert page.radio.checked is True
    assert page.amounts[0].value == "500.0"
    assert all('*="project"' not in selector for selector in page.section.selectors)
    assert page.selectors == [
        'fieldset:has(label:text-is("By project")):has(label:text-is("By milestone")), '
        '[data-test="payment-terms"]:has-text("By project"):has-text("By milestone"), '
        '[data-test="payment-structure"]:has-text("By project"):has-text("By milestone")'
    ]


@pytest.mark.asyncio
async def test_fixed_payment_fails_closed_if_selection_or_amount_is_ambiguous() -> None:
    params = _proposal_params(
        job_type="fixed",
        rate=None,
        bid=500,
        payment_structure="by_project",
    )
    ineffective = _PaymentPage(radio_changes=False)
    assert (await proposals._configure_fixed_payment_terms(ineffective, params))[0] is False
    assert ineffective.amount_queries == 0

    ambiguous = _PaymentPage(amount_inputs=2)
    assert (await proposals._configure_fixed_payment_terms(ambiguous, params))[0] is False

    conflicting = _PaymentPage()
    conflicting.section.project_radio.checked = True
    conflicting.section.milestone_radio.checked = True
    assert (await proposals._configure_fixed_payment_terms(conflicting, params))[0] is False


class _MilestoneRow:
    def __init__(self) -> None:
        self.description = _AmountInput()
        self.due_date = _AmountInput()
        self.amount = _AmountInput()

    async def query_selector(self, selector: str):
        if "description" in selector:
            return self.description
        if "due" in selector or "date" in selector:
            return self.due_date
        if "amount" in selector:
            return self.amount
        return None


class _MilestonePage:
    def __init__(self, row_count: int) -> None:
        self.rows = [_MilestoneRow() for _ in range(row_count)]
        self.section = _PaymentSection(rows=self.rows)

    async def query_selector_all(self, selector: str) -> list[Any]:
        return [self.section] if "payment-terms" in selector else []


@pytest.mark.asyncio
async def test_one_exact_milestone_is_selected_filled_and_read_back() -> None:
    params = _proposal_params(
        job_type="fixed",
        rate=None,
        bid=500,
        payment_structure="by_milestone",
        milestones=[
            {"description": "Initial audit", "due_date": "2026-09-01", "amount": 500},
        ],
    )
    page = _MilestonePage(row_count=1)
    assert await proposals._configure_fixed_payment_terms(page, params) == (True, None)
    assert page.rows[0].description.value == "Initial audit"
    assert page.rows[0].due_date.value == "2026-09-01"
    assert page.rows[0].amount.value == "500.0"

    missing_row = _MilestonePage(row_count=0)
    assert (await proposals._configure_fixed_payment_terms(missing_row, params))[0] is False


class _WarningCheckbox:
    def __init__(self) -> None:
        self.checked = False

    async def check(self) -> None:
        self.checked = True

    async def is_checked(self) -> bool:
        return self.checked


class _WarningButton:
    def __init__(self) -> None:
        self.clicked = False

    async def click(self) -> None:
        self.clicked = True


class _WarningDialog:
    def __init__(self, *, checkbox: bool = True, button: bool = True, exact_text: bool = True) -> None:
        self.checkbox = _WarningCheckbox() if checkbox else None
        self.button = _WarningButton() if button else None
        self.exact_text = exact_text
        self.selectors: list[str] = []

    async def text_content(self) -> str:
        if self.exact_text:
            return "3 things you need to know Yes, I understand."
        return "Another dialog"

    async def query_selector_all(self, selector: str) -> list[Any]:
        self.selectors.append(selector)
        if 'type="checkbox"' in selector:
            return [self.checkbox] if self.checkbox else []
        if 'text-is("Continue")' in selector:
            return [self.button] if self.button else []
        return []


class _WarningPage:
    def __init__(self, dialogs: list[_WarningDialog] | None = None) -> None:
        self.dialogs = dialogs or []
        self.selectors: list[str] = []

    async def query_selector_all(self, selector: str) -> list[Any]:
        self.selectors.append(selector)
        return self.dialogs if selector == '[role="dialog"]' else []


@pytest.mark.asyncio
async def test_fixed_price_warning_never_queries_a_broad_checkbox_or_continue() -> None:
    page = _WarningPage()
    assert await proposals._acknowledge_fixed_price_warning(page) is False
    assert page.selectors == ['[role="dialog"]']

    dialog = _WarningDialog()
    assert await proposals._acknowledge_fixed_price_warning(_WarningPage([dialog])) is True
    assert dialog.checkbox and dialog.checkbox.checked is True
    assert dialog.button and dialog.button.clicked is True
    segments = [segment.strip() for selector in dialog.selectors for segment in selector.split(",")]
    assert 'input[type="checkbox"]' not in segments
    assert 'button:has-text("Continue")' not in segments

    split_controls = _WarningPage(
        [
            _WarningDialog(button=False),
            _WarningDialog(checkbox=False, exact_text=False),
        ]
    )
    assert await proposals._acknowledge_fixed_price_warning(split_controls) is False


class _ConfirmationPage:
    def __init__(self, url: str, text: str = "Proposal submitted successfully") -> None:
        self.url = url
        self.text = text

    async def query_selector(self, selector: str):
        return _Text(self.text) if selector == "body" else None


class _Link(_Text):
    def __init__(self, text: str, href: str) -> None:
        super().__init__(text)
        self.href = href

    async def get_attribute(self, name: str) -> str | None:
        return self.href if name == "href" else None


class _StoredProposalPage:
    def __init__(self) -> None:
        self.url = "https://www.upwork.com/nx/proposals/1111111111111111111"

    async def goto(self, url: str, **_kwargs: Any) -> None:
        self.url = url

    async def query_selector(self, selector: str):
        if '[data-test="job-title"]' in selector:
            return _Text("Google Ads audit")
        if 'a[href^="/jobs/~"]' in selector:
            return _Link("Google Ads audit", "/jobs/~abc123")
        if "proposal-status" in selector:
            return _Text("submitted")
        if selector == "body":
            return _Text("Submitted proposal")
        return None

    async def query_selector_all(self, _selector: str) -> list[Any]:
        return []


@pytest.mark.asyncio
async def test_stored_proposal_readback_extracts_exact_job_identity() -> None:
    details = await proposals._get_proposal_details_on_page(
        "https://www.upwork.com/nx/proposals/1111111111111111111",
        _StoredProposalPage(),
    )
    assert details["proposal_id"] == "1111111111111111111"
    assert details["job_url"] == "https://www.upwork.com/jobs/~abc123"
    assert details["job_id"] == "~abc123"
    assert details["job_title"] == "Google Ads audit"
    assert details["status"] == "submitted"


def _approved_identity() -> dict[str, str]:
    return {
        "job_url": "https://www.upwork.com/jobs/~abc123",
        "job_id": "~abc123",
        "form_url": "https://www.upwork.com/nx/proposals/job/~abc123/apply",
        "job_title": "Google Ads audit",
        "job_type": "hourly",
        "cover_letter": "Exact approved copy",
        "price_amount": "63.00",
    }


@pytest.mark.asyncio
async def test_success_false_and_banner_do_not_confirm_without_exact_proposal_readback(
    monkeypatch,
) -> None:
    page = _ConfirmationPage(
        "https://www.upwork.com/nx/proposals/job/~abc123/apply?success=false"
    )

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(proposals.asyncio, "sleep", no_sleep)
    result = await proposals._proposal_confirmation(page, _approved_identity(), timeout_seconds=0)
    assert result["confirmed"] is False
    assert result["success_query"] is False
    assert proposals._success_query_is_true("https://www.upwork.com/nx/proposals/1?successful=true") is False
    assert proposals._success_query_is_true("https://www.upwork.com/nx/proposals/1?success") is True


@pytest.mark.asyncio
async def test_success_requires_same_stored_proposal_job_identity(monkeypatch) -> None:
    page = _ConfirmationPage(
        "https://www.upwork.com/nx/proposals/1111111111111111111?success=true"
    )

    async def wrong_details(_url: str, _page: Any) -> dict[str, Any]:
        return {
            "proposal_id": "1111111111111111111",
            "job_url": "https://www.upwork.com/jobs/~different",
            "job_id": "~different",
            "job_title": "Google Ads audit",
            "status": "submitted",
        }

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(proposals, "_get_proposal_details_on_page", wrong_details)
    monkeypatch.setattr(proposals.asyncio, "sleep", no_sleep)
    result = await proposals._proposal_confirmation(page, _approved_identity(), timeout_seconds=0)
    assert result["confirmed"] is False
    assert result["success_query"] is True


@pytest.mark.asyncio
async def test_exact_stored_proposal_identity_confirms_without_success_query(monkeypatch) -> None:
    page = _ConfirmationPage(
        "https://www.upwork.com/nx/proposals/1111111111111111111",
        text="Active proposal",
    )

    async def matching_details(_url: str, _page: Any) -> dict[str, Any]:
        return {
            "proposal_id": "1111111111111111111",
            "job_url": "https://www.upwork.com/jobs/~abc123",
            "job_id": "~abc123",
            "job_title": "Google Ads audit",
            "status": "active",
            "cover_letter": "Exact approved copy",
            "bid": "$63.00/hr",
        }

    monkeypatch.setattr(proposals, "_get_proposal_details_on_page", matching_details)
    result = await proposals._proposal_confirmation(page, _approved_identity(), timeout_seconds=0)
    assert result["confirmed"] is True
    assert result["proposal_id"] == "1111111111111111111"
    assert result["success_query"] is False


@pytest.mark.asyncio
async def test_same_job_old_proposal_with_different_copy_or_price_does_not_confirm(
    monkeypatch,
) -> None:
    page = _ConfirmationPage("https://www.upwork.com/nx/proposals/1111111111111111111")

    async def old_details(_url: str, _page: Any) -> dict[str, Any]:
        return {
            "proposal_id": "1111111111111111111",
            "job_url": "https://www.upwork.com/jobs/~abc123",
            "job_id": "~abc123",
            "job_title": "Google Ads audit",
            "status": "submitted",
            "cover_letter": "Old different copy",
            "bid": "$5.00/hr",
        }

    monkeypatch.setattr(proposals, "_get_proposal_details_on_page", old_details)
    result = await proposals._proposal_confirmation(page, _approved_identity(), timeout_seconds=0)
    assert result["confirmed"] is False


@pytest.mark.asyncio
async def test_owner_readback_browser_failure_is_terminal_unconfirmed(monkeypatch) -> None:
    page = _ConfirmationPage("https://www.upwork.com/nx/proposals/1111111111111111111")

    async def failed_readback(_url: str, _page: Any) -> dict[str, Any]:
        raise TimeoutError("navigation timed out")

    monkeypatch.setattr(proposals, "_get_proposal_details_on_page", failed_readback)
    result = await proposals._proposal_confirmation(page, _approved_identity(), timeout_seconds=0)
    assert result["confirmed"] is False
    assert result["url"].endswith("1111111111111111111")
