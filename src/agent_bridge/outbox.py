"""Transactional outbox helpers.

These functions deliberately accept an existing SQLite connection.  The caller
therefore controls the transaction that commits a task mutation and its
delivery intent together.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional


@dataclass(frozen=True)
class OutboxItem:
    id: int
    idempotency_key: str
    kind: str
    payload: Mapping[str, Any]
    due_at: str
    attempts: int


def enqueue(
    connection: sqlite3.Connection,
    idempotency_key: str,
    kind: str,
    payload: Mapping[str, Any],
    due_at: Optional[str] = None,
) -> None:
    """Record one idempotent delivery intent in the caller's transaction."""
    connection.execute(
        "INSERT INTO outbox(idempotency_key, kind, payload_json, due_at) VALUES (?, ?, ?, ?)",
        (
            idempotency_key,
            kind,
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            due_at or utc_now(),
        ),
    )


def due_items(
    connection: sqlite3.Connection,
    now: Optional[str] = None,
    limit: int = 100,
) -> Iterable[OutboxItem]:
    """Return incomplete outbox work due at *now*, in deterministic order."""
    if limit < 1:
        raise ValueError("limit must be positive")
    rows = connection.execute(
        "SELECT id, idempotency_key, kind, payload_json, due_at, attempts "
        "FROM outbox WHERE completed_at IS NULL AND due_at <= ? ORDER BY due_at, id LIMIT ?",
        (now or utc_now(), limit),
    )
    return tuple(
        OutboxItem(
            id=row["id"],
            idempotency_key=row["idempotency_key"],
            kind=row["kind"],
            payload=json.loads(row["payload_json"]),
            due_at=row["due_at"],
            attempts=row["attempts"],
        )
        for row in rows
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
