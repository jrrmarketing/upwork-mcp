"""Tests for exact one-time Upwork approvals."""

from pathlib import Path

import pytest

from upwork_mcp.prepared_actions import approve_action, authorize_action, consume_action, prepare_action


def _payload(rate=63):
    return {
        "job_url": "https://www.upwork.com/jobs/~abc",
        "cover_letter": "Exact approved copy",
        "rate": rate,
        "boost_connects": 0,
    }


def test_prepared_action_needs_matching_owner_approval_and_is_one_time(tmp_path: Path):
    path = tmp_path / "ledger.sqlite3"
    prepared = prepare_action("proposal", _payload(), path=path)

    with pytest.raises(ValueError, match="owner approval"):
        authorize_action(prepared["action_id"], "proposal", _payload(), path=path)

    approve_action(
        prepared["action_id"],
        prepared["approval_sha256"],
        owner_approval_reference="user-turn-approved",
        path=path,
    )
    assert authorize_action(prepared["action_id"], "proposal", _payload(), path=path)["authorized"] is True
    assert consume_action(prepared["action_id"], path=path)["consumed"] is True

    with pytest.raises(ValueError, match="already been consumed"):
        authorize_action(prepared["action_id"], "proposal", _payload(), path=path)


def test_changed_rate_invalidates_prepared_approval(tmp_path: Path):
    path = tmp_path / "ledger.sqlite3"
    prepared = prepare_action("proposal", _payload(), path=path)
    approve_action(
        prepared["action_id"],
        prepared["approval_sha256"],
        owner_approval_reference="approved",
        path=path,
    )
    with pytest.raises(ValueError, match="changed after approval"):
        authorize_action(prepared["action_id"], "proposal", _payload(rate=64), path=path)


def test_duplicate_prepare_reuses_pending_idempotency_key(tmp_path: Path):
    path = tmp_path / "ledger.sqlite3"
    first = prepare_action("message", {"room_id": "room1", "message": "Hi"}, path=path)
    second = prepare_action("message", {"room_id": "room1", "message": "Hi"}, path=path)
    assert second["action_id"] == first["action_id"]
    assert second["reused"] is True


def test_consumed_payload_can_only_return_as_a_new_action(tmp_path: Path):
    path = tmp_path / "ledger.sqlite3"
    payload = {"room_id": "room1", "message": "Hi"}
    first = prepare_action("message", payload, path=path)
    approve_action(
        first["action_id"],
        first["approval_sha256"],
        owner_approval_reference="approved",
        path=path,
    )
    consume_action(first["action_id"], path=path)

    second = prepare_action("message", payload, path=path)
    assert second["action_id"] != first["action_id"]
    assert second["approved"] is False
