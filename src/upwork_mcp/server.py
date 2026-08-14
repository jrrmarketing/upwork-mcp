"""Upwork MCP server with JRR screening and approval-gated actions."""

from __future__ import annotations

import argparse
import asyncio
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .browser.auth import check_session, login_interactive, logout
from .browser.client import close_browser, get_browser
from .ledger import bidding_report, record_outcome
from .prepared_actions import approve_action, consume_action
from .tools.contracts import ContractsParams, get_contract_details, get_contracts, get_work_diary
from .tools.invitations import (
    DeclineInvitationParams,
    InvitationsParams,
    decline_invitation,
    get_invitations,
    prepare_invitation_decline_from_live,
)
from .tools.jobs import (
    JobDetailsParams,
    JobSearchParams,
    get_job_details,
    screen_job,
    search_jobs,
)
from .tools.management import (
    PrepareProposalParams,
    audit_live_proposals,
    find_opportunities,
    prepare_proposal,
)
from .tools.messages import (
    MessagesParams,
    SendMessageParams,
    get_conversation_messages,
    get_messages,
    get_unread_count,
    prepare_message_from_live,
    send_message,
)
from .tools.profile import get_connects_balance, get_my_profile, get_profile_stats
from .tools.proposals import (
    DiscoveryStatus,
    FixedPriceMilestone,
    InspectProposalFormParams,
    ProposalsParams,
    RateIncreaseControlStatus,
    SubmitProposalParams,
    WithdrawProposalParams,
    get_proposal_details,
    get_proposals,
    inspect_proposal_form,
    prepare_proposal_withdrawal,
    submit_proposal,
    withdraw_proposal,
)

mcp = FastMCP(
    name="upwork-mcp",
    instructions=(
        "JRR Upwork management. Discovery, screening, audits, and preparation are read-only. "
        "Every proposal, message, withdrawal, and invitation decline requires an unexpired exact-payload "
        "approval record, then owner-system readback. Never infer approval or Connect spend."
    ),
)


# ---------------------------------------------------------------------------
# Job discovery and decision support (read-only except local ledger records)
# ---------------------------------------------------------------------------


@mcp.tool()
async def upwork_search_jobs(
    query: Annotated[str, Field(description="Search keywords")],
    category: Annotated[str | None, Field(description="Optional Upwork category token")] = None,
    budget_min: Annotated[float | None, Field(description="Minimum advertised budget/rate", ge=0)] = None,
    budget_max: Annotated[float | None, Field(description="Maximum advertised budget/rate", ge=0)] = None,
    experience_level: Annotated[
        Literal["entry", "intermediate", "expert"] | None,
        Field(description="entry, intermediate, or expert"),
    ] = None,
    job_type: Annotated[Literal["hourly", "fixed"] | None, Field(description="hourly or fixed")] = None,
    search_mode: Annotated[
        Literal["best_matches", "most_recent"],
        Field(description="best_matches or most_recent"),
    ] = "best_matches",
    limit: Annotated[int, Field(description="Maximum results", ge=1, le=50)] = 20,
) -> list[dict]:
    """Search one live Upwork discovery view without applying or spending Connects."""
    return await search_jobs(
        JobSearchParams(
            query=query,
            category=category,
            budget_min=budget_min,
            budget_max=budget_max,
            experience_level=experience_level,
            job_type=job_type,
            search_mode=search_mode,
            limit=limit,
        )
    )


@mcp.tool()
async def upwork_get_job_details(
    job_url: Annotated[str, Field(description="Full Upwork job URL or job ID")],
) -> dict:
    """Read a live job, client economics, activity, competition, and Connect cost."""
    return await get_job_details(JobDetailsParams(job_url=job_url))


@mcp.tool()
async def upwork_screen_job(
    job_url: Annotated[str, Field(description="Full Upwork job URL or job ID")],
    profile_hourly_rate: Annotated[float, Field(ge=50)] = 63,
    minimum_hourly_rate: Annotated[float, Field(ge=50)] = 50,
    minimum_fixed_fee: Annotated[float | None, Field(gt=0)] = None,
) -> dict:
    """Classify one live job as strong fit, fit, price conversion, speculative, or skip."""
    return await screen_job(
        job_url,
        profile_hourly_rate=profile_hourly_rate,
        minimum_hourly_rate=minimum_hourly_rate,
        minimum_fixed_fee=minimum_fixed_fee,
    )


@mcp.tool()
async def upwork_find_opportunities(
    query: Annotated[str, Field(min_length=1, description="Search keywords")],
    limit_per_view: Annotated[int, Field(ge=1, le=20)] = 5,
    include_skips: bool = False,
    profile_hourly_rate: Annotated[float, Field(ge=50)] = 63,
    minimum_hourly_rate: Annotated[float, Field(ge=50)] = 50,
    minimum_fixed_fee: Annotated[float | None, Field(gt=0)] = None,
) -> dict:
    """Read Best Matches and Most Recent, hydrate each job, and rank realistic opportunities."""
    return await find_opportunities(
        query,
        limit_per_view=limit_per_view,
        include_skips=include_skips,
        profile_hourly_rate=profile_hourly_rate,
        minimum_hourly_rate=minimum_hourly_rate,
        minimum_fixed_fee=minimum_fixed_fee,
    )


# ---------------------------------------------------------------------------
# Profile (read-only)
# ---------------------------------------------------------------------------


@mcp.tool()
async def upwork_get_my_profile() -> dict:
    """Read the current freelancer profile; does not change rate or profile content."""
    return await get_my_profile()


@mcp.tool()
async def upwork_get_connects_balance() -> dict:
    """Read the live Connects balance."""
    return await get_connects_balance()


@mcp.tool()
async def upwork_get_profile_stats() -> dict:
    """Read current profile earnings and work-history statistics."""
    return await get_profile_stats()


# ---------------------------------------------------------------------------
# Proposals: inspect -> prepare -> approve -> commit
# ---------------------------------------------------------------------------


@mcp.tool()
async def upwork_get_proposals(
    status: Annotated[
        Literal["active", "submitted", "archived", "all"],
        Field(description="active, submitted, archived, or all"),
    ] = "active",
    limit: Annotated[int, Field(ge=1, le=50)] = 20,
) -> list[dict]:
    """Read current submitted proposals without withdrawing or editing them."""
    return await get_proposals(ProposalsParams(status=status, limit=limit))


@mcp.tool()
async def upwork_get_proposal_details(
    proposal_url: Annotated[str, Field(description="Full Upwork proposal URL")],
) -> dict:
    """Read one proposal and its visible status."""
    return await get_proposal_details(proposal_url)


@mcp.tool()
async def upwork_audit_proposals(
    status: Annotated[
        Literal["active", "submitted", "archived", "all"],
        Field(description="active, submitted, archived, or all"),
    ] = "all",
    limit: Annotated[int, Field(ge=1, le=50)] = 50,
    stale_after_days: Annotated[int, Field(ge=7, le=180)] = 14,
) -> dict:
    """Review proposal maintenance; old unviewed proposals are not withdrawn merely for tidiness."""
    return await audit_live_proposals(status=status, limit=limit, stale_after_days=stale_after_days)


@mcp.tool()
async def upwork_inspect_proposal_form(
    job_url: Annotated[str, Field(description="Full individual Upwork job or application URL")],
) -> dict:
    """Open and read the live application form without filling or submitting it."""
    return await inspect_proposal_form(InspectProposalFormParams(job_url=job_url))


@mcp.tool()
async def upwork_prepare_proposal(
    job_url: str,
    cover_letter: str,
    rate: float | None = None,
    bid: float | None = None,
    payment_structure: Literal["by_project", "by_milestone"] | None = None,
    milestones: list[dict[str, Any]] | None = None,
    answers: list[str] | None = None,
    duration: Literal[
        "Less than 1 month",
        "1 to 3 months",
        "3 to 6 months",
        "More than 6 months",
    ] | None = None,
    profile_highlights: list[str] | None = None,
    boost_connects: Annotated[int, Field(ge=0)] = 0,
    profile_hourly_rate: Annotated[float, Field(ge=50)] = 63,
    minimum_hourly_rate: Annotated[float, Field(ge=50)] = 50,
    minimum_fixed_fee: Annotated[float | None, Field(gt=0)] = None,
) -> dict:
    """Inspect the live form and create an exact expiring proposal approval record; never submits."""
    return await prepare_proposal(
        PrepareProposalParams(
            job_url=job_url,
            cover_letter=cover_letter,
            rate=rate,
            bid=bid,
            payment_structure=payment_structure,
            milestones=[FixedPriceMilestone.model_validate(item) for item in (milestones or [])],
            answers=answers or [],
            duration=duration,
            profile_highlights=profile_highlights or [],
            boost_connects=boost_connects,
            profile_hourly_rate=profile_hourly_rate,
            minimum_hourly_rate=minimum_hourly_rate,
            minimum_fixed_fee=minimum_fixed_fee,
        )
    )


@mcp.tool()
async def upwork_approve_prepared_action(
    action_id: Annotated[str, Field(description="Prepared one-time action ID")],
    approval_sha256: Annotated[str, Field(pattern=r"^[0-9a-fA-F]{64}$")],
    owner_approval_reference: Annotated[
        str,
        Field(min_length=1, max_length=200, description="Reference to the fresh exact-copy approval"),
    ],
) -> dict:
    """Arm one unchanged prepared action locally after the owner explicitly approves its exact payload."""
    return approve_action(
        action_id,
        approval_sha256,
        owner_approval_reference=owner_approval_reference,
    )


@mcp.tool()
async def upwork_submit_prepared_proposal(
    action_id: str,
    job_url: str,
    job_id: str,
    form_url: str,
    job_title: str,
    job_type: Literal["hourly", "fixed"],
    cover_letter: str,
    fee_net_text: list[str],
    fee_net_status: DiscoveryStatus,
    fee_net_price_amount: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]{2}$")],
    fee_net_source: Literal["scoped_reversible_price_preflight"],
    boost_auction_text: list[str],
    boost_auction_status: DiscoveryStatus,
    screening_questions_status: DiscoveryStatus,
    duration_options_status: DiscoveryStatus,
    available_profile_highlights_status: DiscoveryStatus,
    base_connects_status: DiscoveryStatus,
    rate_increase_control_status: RateIncreaseControlStatus,
    rate: Annotated[float | None, Field(ge=50)] = None,
    bid: float | None = None,
    payment_structure: Literal["by_project", "by_milestone"] | None = None,
    milestones: list[dict[str, Any]] | None = None,
    answers: list[str] | None = None,
    screening_questions: list[str] | None = None,
    duration: Literal[
        "Less than 1 month",
        "1 to 3 months",
        "3 to 6 months",
        "More than 6 months",
    ] | None = None,
    profile_highlights: list[str] | None = None,
    base_connects: int | None = None,
    boost_connects: Annotated[int, Field(ge=0)] = 0,
) -> dict:
    """Commit one unexpired approved proposal and consume its action ID after Upwork readback."""
    params = SubmitProposalParams(
        action_id=action_id,
        job_url=job_url,
        job_id=job_id,
        form_url=form_url,
        job_title=job_title,
        job_type=job_type,
        cover_letter=cover_letter,
        fee_net_text=fee_net_text,
        fee_net_status=fee_net_status,
        fee_net_price_amount=fee_net_price_amount,
        fee_net_source=fee_net_source,
        boost_auction_text=boost_auction_text,
        boost_auction_status=boost_auction_status,
        rate=rate,
        bid=bid,
        payment_structure=payment_structure,
        milestones=[FixedPriceMilestone.model_validate(item) for item in (milestones or [])],
        answers=answers or [],
        screening_questions=screening_questions or [],
        screening_questions_status=screening_questions_status,
        duration=duration,
        duration_options_status=duration_options_status,
        profile_highlights=profile_highlights or [],
        available_profile_highlights_status=available_profile_highlights_status,
        base_connects=base_connects,
        base_connects_status=base_connects_status,
        boost_connects=boost_connects,
        rate_increase_frequency="Never",
        rate_increase_control_status=rate_increase_control_status,
    )
    result = await submit_proposal(params)
    if result.get("status") == "submitted" and result.get("owner_system_readback", {}).get("confirmed"):
        result["prepared_action"] = consume_action(action_id)
        try:
            result["local_outcome"] = record_outcome(job_url, "submitted")
        except ValueError as error:
            result["local_outcome"] = {"recorded": False, "reason": str(error)}
    return result


@mcp.tool()
async def upwork_prepare_withdrawal(
    proposal_url: str,
    reason: str | None = None,
) -> dict:
    """Read the proposal and prepare one exact withdrawal; never withdraws."""
    return await prepare_proposal_withdrawal(proposal_url, reason)


@mcp.tool()
async def upwork_confirm_withdrawal(
    action_id: str,
    proposal_url: str,
    proposal_id: str,
    job_title: str,
    proposal_status: str,
    reason: str | None = None,
) -> dict:
    """Commit one unexpired approved withdrawal and consume its action after Upwork readback."""
    result = await withdraw_proposal(
        WithdrawProposalParams(
            action_id=action_id,
            proposal_url=proposal_url,
            proposal_id=proposal_id,
            job_title=job_title,
            proposal_status=proposal_status,
            reason=reason,
        )
    )
    if result.get("status") == "withdrawn" and result.get("owner_system_readback", {}).get("confirmed"):
        result["prepared_action"] = consume_action(action_id)
    return result


# ---------------------------------------------------------------------------
# Messages: read -> prepare -> approve -> commit
# ---------------------------------------------------------------------------


@mcp.tool()
async def upwork_get_messages(
    room_id: str | None = None,
    unread_only: bool = False,
    limit: Annotated[int, Field(ge=1, le=50)] = 20,
) -> list[dict]:
    """Read inbox conversations; room_id narrows to one conversation."""
    return await get_messages(MessagesParams(room_id=room_id, unread_only=unread_only, limit=limit))


@mcp.tool()
async def upwork_get_conversation(
    room_id: str,
    limit: Annotated[int, Field(ge=1, le=100)] = 50,
) -> dict:
    """Read visible messages in one conversation and report whether history may be incomplete."""
    return await get_conversation_messages(room_id, limit)


@mcp.tool()
async def upwork_prepare_message(room_id: str, message: str) -> dict:
    """Read the exact recipient, validate copy, and prepare approval state; never sends."""
    return await prepare_message_from_live(room_id, message)


@mcp.tool()
async def upwork_send_prepared_message(
    action_id: str,
    room_url: str,
    room_id: str,
    contact_name: str,
    message: str,
    history_snapshot_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")],
    history_record_count: Annotated[int, Field(ge=0)],
    history_completeness_proof: Literal["exact_owner_complete_boundary"],
    last_message_identity_sha256: Annotated[
        str | None,
        Field(pattern=r"^[0-9a-f]{64}$"),
    ] = None,
) -> dict:
    """Send one unexpired approved exact-copy message and consume its action after readback."""
    result = await send_message(
        SendMessageParams(
            action_id=action_id,
            room_url=room_url,
            room_id=room_id,
            contact_name=contact_name,
            message=message,
            history_snapshot_sha256=history_snapshot_sha256,
            history_record_count=history_record_count,
            last_message_identity_sha256=last_message_identity_sha256,
            history_completeness_proof=history_completeness_proof,
        )
    )
    if result.get("status") == "sent" and result.get("owner_system_readback", {}).get("confirmed"):
        result["prepared_action"] = consume_action(action_id)
    return result


@mcp.tool()
async def upwork_get_unread_count() -> dict:
    """Read the current Upwork unread-message count."""
    return await get_unread_count()


# ---------------------------------------------------------------------------
# Invitations: read -> prepare -> approve -> commit
# ---------------------------------------------------------------------------


@mcp.tool()
async def upwork_get_invitations(
    limit: Annotated[int, Field(ge=1, le=50)] = 20,
) -> list[dict]:
    """Read current invitations without accepting or declining them."""
    return await get_invitations(InvitationsParams(limit=limit))


@mcp.tool()
async def upwork_prepare_invitation_decline(
    invitation_url: str,
    reason: Literal["Not interested in work described"] = "Not interested in work described",
    note: str | None = None,
) -> dict:
    """Prepare one decline with future-invitation blocking locked off; never declines."""
    return await prepare_invitation_decline_from_live(
        invitation_url,
        reason=reason,
        note=note,
    )


@mcp.tool()
async def upwork_confirm_invitation_decline(
    action_id: str,
    invitation_url: str,
    invitation_id: str,
    job_title: str,
    invitation_status: str,
    reason: Literal["Not interested in work described"] = "Not interested in work described",
    note: str | None = None,
) -> dict:
    """Commit one unexpired approved decline and consume its action after Upwork readback."""
    params = DeclineInvitationParams(
        action_id=action_id,
        invitation_url=invitation_url,
        invitation_id=invitation_id,
        job_title=job_title,
        invitation_status=invitation_status,
        reason=reason,
        note=note,
        block_future_invitations=False,
    )
    result = await decline_invitation(params)
    if result.get("status") == "declined" and result.get("owner_system_readback", {}).get("confirmed"):
        result["prepared_action"] = consume_action(action_id)
    return result


# ---------------------------------------------------------------------------
# Contracts and learning loop
# ---------------------------------------------------------------------------


@mcp.tool()
async def upwork_get_contracts(
    status: Annotated[
        Literal["active", "ended", "all"],
        Field(description="active, ended, or all"),
    ] = "active",
    limit: Annotated[int, Field(ge=1, le=50)] = 20,
) -> list[dict]:
    """Read contracts without changing terms or work diaries."""
    return await get_contracts(ContractsParams(status=status, limit=limit))


@mcp.tool()
async def upwork_get_contract_details(contract_url: str) -> dict:
    """Read one contract and visible status."""
    return await get_contract_details(contract_url)


@mcp.tool()
async def upwork_get_work_diary(contract_url: str, week_offset: int = 0) -> dict:
    """Read a contract's work diary; does not add, edit, or delete time."""
    return await get_work_diary(contract_url, week_offset)


@mcp.tool()
async def upwork_record_outcome(job_url: str, outcome: str) -> dict:
    """Record an owner-system-verified job outcome in the private local decision ledger."""
    return record_outcome(job_url, outcome)


@mcp.tool()
async def upwork_bidding_report(
    minimum_sample: Annotated[int, Field(ge=3, le=100)] = 5,
) -> dict:
    """Report view/interview/hire rates; never rewrites scoring weights automatically."""
    return bidding_report(minimum_sample=minimum_sample)


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


@mcp.tool()
async def upwork_check_session() -> dict:
    """Check the freelancer session in a disposable serialized browser tab."""
    try:
        logged_in = await check_session()
        return {
            "logged_in": logged_in,
            "message": "Session is valid" if logged_in else "Session expired; run the Upwork login flow.",
        }
    except Exception as error:
        return {"logged_in": False, "error": str(error)}


@mcp.tool()
async def upwork_close_session() -> dict:
    """Disconnect Patchright while leaving the owner's Chrome process running."""
    await close_browser()
    return {"status": "closed"}


def main() -> None:
    parser = argparse.ArgumentParser(description="JRR Upwork MCP")
    parser.add_argument(
        "--login",
        action="store_true",
        help="Open a login tab in an already attached owner Chrome window",
    )
    parser.add_argument("--check", action="store_true", help="Check the attached Upwork session")
    parser.add_argument(
        "--logout",
        action="store_true",
        help="Disconnect automation without closing Chrome or deleting browser data",
    )
    parser.add_argument("--no-headless", action="store_true", help="Compatibility option; browser is attach-only")
    parser.add_argument("--timeout", type=int, default=30000)
    parser.add_argument("--transport", choices=["stdio"], default="stdio")
    args = parser.parse_args()

    if args.login:
        asyncio.run(login_interactive())
        return
    if args.check:
        valid = asyncio.run(check_session())
        print("Session is valid" if valid else "Session expired or invalid")
        raise SystemExit(0 if valid else 1)
    if args.logout:
        asyncio.run(logout())
        return

    get_browser(headless=not args.no_headless, timeout=args.timeout)
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
