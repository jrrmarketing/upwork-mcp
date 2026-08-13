"""One-time local approval records for consequential Upwork actions."""

from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .ledger import default_ledger_path
from .strategy import payload_digest

DEFAULT_EXPIRY_MINUTES = 30


def _path(path: Path | None = None) -> Path:
    return path or default_ledger_path()


def _connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = _path(path).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS prepared_actions (
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
    try:
        db_path.chmod(0o600)
    except OSError:
        pass
    return connection


def prepare_action(
    action_type: str,
    payload: Mapping[str, Any],
    *,
    expires_in_minutes: int = DEFAULT_EXPIRY_MINUTES,
    path: Path | None = None,
) -> dict[str, Any]:
    """Store exact copy/terms for later owner approval without acting externally."""
    if expires_in_minutes < 5 or expires_in_minutes > 120:
        raise ValueError("expires_in_minutes must be between 5 and 120")
    normalized_type = action_type.strip().lower()
    if normalized_type not in {"proposal", "message", "withdrawal", "invitation_decline"}:
        raise ValueError("Unsupported prepared action type")
    prepared_at = datetime.now(UTC)
    expires_at = prepared_at + timedelta(minutes=expires_in_minutes)
    action_id = f"uwa_{secrets.token_urlsafe(12)}"
    digest = payload_digest(payload)
    idempotency_key = payload_digest(
        {"action_type": normalized_type, "payload_sha256": digest, "action_id": action_id}
    )
    serialized = json.dumps(dict(payload), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    with _connect(path) as connection:
        previous = connection.execute(
            """
            SELECT action_id, idempotency_key, expires_at, approved_at, consumed_at
            FROM prepared_actions
            WHERE action_type = ? AND payload_sha256 = ? AND consumed_at IS NULL
            ORDER BY prepared_at DESC
            LIMIT 1
            """,
            (normalized_type, digest),
        ).fetchone()
        if previous and previous["consumed_at"] is None and datetime.fromisoformat(previous["expires_at"]) > prepared_at:
            return {
                "action_id": previous["action_id"],
                "action_type": normalized_type,
                "approval_sha256": digest,
                "idempotency_key": previous["idempotency_key"],
                "expires_at": previous["expires_at"],
                "approved": previous["approved_at"] is not None,
                "reused": True,
            }
        connection.execute(
            """
            INSERT INTO prepared_actions (
                action_id, action_type, payload_json, payload_sha256,
                idempotency_key, prepared_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                action_id,
                normalized_type,
                serialized,
                digest,
                idempotency_key,
                prepared_at.isoformat(),
                expires_at.isoformat(),
            ),
        )
    return {
        "action_id": action_id,
        "action_type": normalized_type,
        "approval_sha256": digest,
        "idempotency_key": idempotency_key,
        "expires_at": expires_at.isoformat(),
        "approved": False,
        "reused": False,
    }


def approve_action(
    action_id: str,
    approval_sha256: str,
    *,
    owner_approval_reference: str,
    path: Path | None = None,
) -> dict[str, Any]:
    """Bind a fresh owner approval to the unchanged prepared payload."""
    now = datetime.now(UTC)
    with _connect(path) as connection:
        row = connection.execute("SELECT * FROM prepared_actions WHERE action_id = ?", (action_id,)).fetchone()
        if row is None:
            raise ValueError("Prepared action was not found")
        if row["consumed_at"]:
            raise ValueError("Prepared action has already been consumed")
        if datetime.fromisoformat(row["expires_at"]) <= now:
            raise ValueError("Prepared action has expired; prepare the current live state again")
        if not secrets.compare_digest(row["payload_sha256"], approval_sha256):
            raise ValueError("Approval digest does not match the prepared payload")
        connection.execute(
            "UPDATE prepared_actions SET approved_at = ?, owner_approval_reference = ? WHERE action_id = ?",
            (now.isoformat(), owner_approval_reference.strip()[:200], action_id),
        )
    return {"action_id": action_id, "approved": True, "approved_at": now.isoformat()}


def authorize_action(
    action_id: str,
    action_type: str,
    payload: Mapping[str, Any],
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    """Validate an approved, unexpired, unchanged, unconsumed action before UI work."""
    now = datetime.now(UTC)
    with _connect(path) as connection:
        row = connection.execute("SELECT * FROM prepared_actions WHERE action_id = ?", (action_id,)).fetchone()
    if row is None:
        raise ValueError("Prepared action was not found")
    if row["action_type"] != action_type.strip().lower():
        raise ValueError("Prepared action type does not match")
    if row["approved_at"] is None:
        raise ValueError("Fresh owner approval has not been recorded")
    if row["consumed_at"] is not None:
        raise ValueError("Prepared action has already been consumed")
    if datetime.fromisoformat(row["expires_at"]) <= now:
        raise ValueError("Prepared action has expired; prepare the current live state again")
    digest = payload_digest(payload)
    if not secrets.compare_digest(row["payload_sha256"], digest):
        raise ValueError("Prepared payload changed after approval")
    return {
        "action_id": action_id,
        "authorized": True,
        "approval_sha256": digest,
        "idempotency_key": row["idempotency_key"],
        "expires_at": row["expires_at"],
    }


def consume_action(action_id: str, *, path: Path | None = None) -> dict[str, Any]:
    """Mark the action consumed only after owner-system success readback."""
    consumed_at = datetime.now(UTC).isoformat()
    with _connect(path) as connection:
        row = connection.execute("SELECT approved_at, consumed_at FROM prepared_actions WHERE action_id = ?", (action_id,)).fetchone()
        if row is None:
            raise ValueError("Prepared action was not found")
        if row["approved_at"] is None:
            raise ValueError("Cannot consume an unapproved action")
        if row["consumed_at"] is not None:
            return {"action_id": action_id, "consumed": False, "consumed_at": row["consumed_at"]}
        connection.execute("UPDATE prepared_actions SET consumed_at = ? WHERE action_id = ?", (consumed_at, action_id))
    return {"action_id": action_id, "consumed": True, "consumed_at": consumed_at}
