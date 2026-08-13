"""Tests for exact one-time Upwork approvals."""

import json
import sqlite3
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

    approval = approve_action(
        prepared["action_id"],
        prepared["approval_sha256"],
        owner_approval_reference="user-turn-approved",
        path=path,
    )
    authorization = authorize_action(prepared["action_id"], "proposal", _payload(), path=path)
    assert authorization["authorized"] is True
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        claimed = connection.execute(
            """
            SELECT payload_json, payload_sha256, idempotency_key,
                   prepared_at, expires_at, approved_at, claimed_at,
                   consumed_at, payload_redacted_at, owner_approval_reference
            FROM prepared_actions WHERE action_id = ?
            """,
            (prepared["action_id"],),
        ).fetchone()
    assert claimed is not None
    assert json.loads(claimed["payload_json"]) == {"redacted": True}
    assert claimed["payload_sha256"] == prepared["approval_sha256"]
    assert claimed["idempotency_key"] == prepared["idempotency_key"]
    assert claimed["prepared_at"]
    assert claimed["expires_at"] == prepared["expires_at"]
    assert claimed["approved_at"] == approval["approved_at"]
    assert claimed["payload_redacted_at"] == authorization["claimed_at"] == claimed["claimed_at"]
    assert claimed["consumed_at"] is None
    assert claimed["owner_approval_reference"] == "user-turn-approved"
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
    consumed = consume_action(first["action_id"], path=path)
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        stored = connection.execute(
            """
            SELECT payload_json, payload_redacted_at, consumed_at
            FROM prepared_actions WHERE action_id = ?
            """,
            (first["action_id"],),
        ).fetchone()
    assert stored is not None
    assert json.loads(stored["payload_json"]) == {"redacted": True}
    assert stored["payload_redacted_at"] == consumed["consumed_at"] == stored["consumed_at"]

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


def test_existing_database_migrates_and_redacts_legacy_terminal_payloads(tmp_path: Path):
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
        connection.executemany(
            """
            INSERT INTO prepared_actions (
                action_id, action_type, payload_json, payload_sha256,
                idempotency_key, prepared_at, expires_at, approved_at,
                consumed_at, owner_approval_reference
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "expired-action",
                    "proposal",
                    '{"cover_letter":"legacy private copy"}',
                    "expired-digest",
                    "expired-key",
                    "2000-01-01T00:00:00+00:00",
                    "2000-01-01T00:30:00+00:00",
                    None,
                    None,
                    None,
                ),
                (
                    "consumed-action",
                    "message",
                    '{"message":"legacy private message"}',
                    "consumed-digest",
                    "consumed-key",
                    "2000-01-01T00:00:00+00:00",
                    "2099-01-01T00:00:00+00:00",
                    "2000-01-01T00:05:00+00:00",
                    "2000-01-01T00:06:00+00:00",
                    "approved",
                ),
            ],
        )

    pending_payload = {"room_id": "room1", "message": "Hi"}
    prepared = prepare_action("message", pending_payload, path=path)
    assert prepared["action_id"]
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        columns = {row[1] for row in connection.execute("PRAGMA table_info(prepared_actions)")}
        terminal_rows = connection.execute(
            """
            SELECT action_id, payload_json, payload_sha256, idempotency_key,
                   prepared_at, consumed_at, payload_redacted_at
            FROM prepared_actions
            WHERE action_id IN ('expired-action', 'consumed-action')
            ORDER BY action_id
            """
        ).fetchall()
        pending = connection.execute(
            "SELECT payload_json, payload_redacted_at FROM prepared_actions WHERE action_id = ?",
            (prepared["action_id"],),
        ).fetchone()
    assert "claimed_at" in columns
    assert "payload_redacted_at" in columns
    assert {row["action_id"] for row in terminal_rows} == {"expired-action", "consumed-action"}
    assert all(json.loads(row["payload_json"]) == {"redacted": True} for row in terminal_rows)
    assert all(row["payload_redacted_at"] for row in terminal_rows)
    assert {
        (row["action_id"], row["payload_sha256"], row["idempotency_key"], row["prepared_at"], row["consumed_at"])
        for row in terminal_rows
    } == {
        ("expired-action", "expired-digest", "expired-key", "2000-01-01T00:00:00+00:00", None),
        (
            "consumed-action",
            "consumed-digest",
            "consumed-key",
            "2000-01-01T00:00:00+00:00",
            "2000-01-01T00:06:00+00:00",
        ),
    }
    assert pending is not None
    assert json.loads(pending["payload_json"]) == pending_payload
    assert pending["payload_redacted_at"] is None


def test_expired_payload_redaction_survives_failed_access(tmp_path: Path):
    path = tmp_path / "ledger.sqlite3"
    prepared = prepare_action("proposal", _payload(), path=path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE prepared_actions SET expires_at = ? WHERE action_id = ?",
            ("2000-01-01T00:00:00+00:00", prepared["action_id"]),
        )

    with pytest.raises(ValueError, match="expired"):
        approve_action(
            prepared["action_id"],
            prepared["approval_sha256"],
            owner_approval_reference="approved",
            path=path,
        )

    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        stored = connection.execute(
            "SELECT payload_json, payload_redacted_at FROM prepared_actions WHERE action_id = ?",
            (prepared["action_id"],),
        ).fetchone()
    assert stored is not None
    assert json.loads(stored["payload_json"]) == {"redacted": True}
    assert stored["payload_redacted_at"] is not None
