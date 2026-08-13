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


def test_authorization_atomically_claims_action_and_blocks_replay(tmp_path: Path):
    path = tmp_path / "ledger.sqlite3"
    payload = {"room_id": "room1", "message": "Hi"}
    prepared = prepare_action("message", payload, path=path)
    approve_action(
        prepared["action_id"],
        prepared["approval_sha256"],
        owner_approval_reference="approved",
        path=path,
    )

    first = authorize_action(prepared["action_id"], "message", payload, path=path)
    assert first["claimed_at"]
    with pytest.raises(ValueError, match="already been claimed"):
        authorize_action(prepared["action_id"], "message", payload, path=path)


def test_claimed_unknown_action_requires_fresh_prepare_and_approval(tmp_path: Path):
    path = tmp_path / "ledger.sqlite3"
    payload = {"room_id": "room1", "message": "Hi"}
    first = prepare_action("message", payload, path=path)
    approve_action(
        first["action_id"],
        first["approval_sha256"],
        owner_approval_reference="approved",
        path=path,
    )
    authorize_action(first["action_id"], "message", payload, path=path)

    second = prepare_action("message", payload, path=path)
    assert second["action_id"] != first["action_id"]
    assert second["approved"] is False


def test_existing_database_is_migrated_with_claimed_state(tmp_path: Path):
    import sqlite3

    path = tmp_path / "ledger.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE prepared_actions (
                action_id TEXT PRIMARY KEY,
                action_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                prepared_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                approved_at TEXT,
                consumed_at TEXT,
                owner_approval_reference TEXT
            )
            """
        )

    prepared = prepare_action("message", {"room_id": "room1", "message": "Hi"}, path=path)
    assert prepared["action_id"]
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(prepared_actions)")}
    assert "claimed_at" in columns
