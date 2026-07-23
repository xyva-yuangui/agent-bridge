"""Short-lived, lease-protected transactional outbox dispatcher."""

from __future__ import annotations

import os
import random
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping, Optional

from .delivery import DeliveryChannel, EVIDENCE_RANK
from .models import DeliveryStatus
from .outbox import OutboxItem, due_items, utc_now
from .paths import get_data_root
from .store import Store


LEASE_NAME = "delivery"
MAX_BURST_SECONDS = 30.0
DEFAULT_LEASE_SECONDS = 35.0
DEFAULT_BATCH_SIZE = 32
DEFAULT_MAX_ATTEMPTS = 5
MAX_RETRY_SECONDS = 300.0


@dataclass(frozen=True)
class DispatchReport:
    """The bounded, observable outcome of one dispatcher invocation."""

    acquired: bool
    processed: int = 0
    delivered: int = 0
    retried: int = 0
    failed: int = 0
    coalesced: int = 0
    timed_out: bool = False

    @property
    def lease_acquired(self) -> bool:
        return self.acquired


class Dispatcher:
    """Drains due outbox work through explicitly supplied delivery channels."""

    def __init__(
        self,
        store: Store,
        channels: Optional[Mapping[str, DeliveryChannel]] = None,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        after_effect: Optional[Callable[[OutboxItem, DeliveryStatus], None]] = None,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self.store = store
        self.channels = dict(channels or {})
        self.batch_size = batch_size
        self.max_attempts = max_attempts
        self.lease_seconds = lease_seconds
        self.after_effect = after_effect
        self.owner = uuid.uuid4().hex

    def acquire_lease(self) -> bool:
        """Claim the expiring singleton lease without relying on PID liveness."""
        now = utc_now()
        expires_at = _after_seconds(self.lease_seconds)
        with self.store.transaction(immediate=True) as connection:
            inserted = connection.execute(
                "INSERT OR IGNORE INTO dispatcher_leases(name, owner, acquired_at, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (LEASE_NAME, self.owner, now, expires_at),
            ).rowcount
            if inserted:
                return True
            reclaimed = connection.execute(
                "UPDATE dispatcher_leases SET owner = ?, acquired_at = ?, expires_at = ? "
                "WHERE name = ? AND expires_at <= ?",
                (self.owner, now, expires_at, LEASE_NAME, now),
            ).rowcount
            return reclaimed == 1

    def release_lease(self) -> None:
        """Release only this dispatcher's lease; never delete a reclaimed lease."""
        with self.store.transaction(immediate=True) as connection:
            connection.execute(
                "DELETE FROM dispatcher_leases WHERE name = ? AND owner = ?", (LEASE_NAME, self.owner)
            )

    def run_burst(self, deadline_seconds: float = MAX_BURST_SECONDS) -> DispatchReport:
        """Deliver due intents until idle or the hard thirty-second burst limit."""
        duration = min(MAX_BURST_SECONDS, max(0.0, float(deadline_seconds)))
        if not self.acquire_lease():
            return DispatchReport(acquired=False)
        started = time.monotonic()
        processed = delivered = retried = failed = coalesced = 0
        timed_out = False
        try:
            while True:
                if time.monotonic() - started >= duration:
                    timed_out = True
                    break
                items = tuple(due_items(self.store.connection, limit=self.batch_size))
                if not items:
                    break
                for group in _coalesced_groups(items):
                    if time.monotonic() - started >= duration:
                        timed_out = True
                        break
                    processed += len(group)
                    coalesced += len(group) - 1
                    outcome = self._deliver_group(group)
                    delivered += outcome[0]
                    retried += outcome[1]
                    failed += outcome[2]
                if timed_out:
                    break
        finally:
            self.release_lease()
        return DispatchReport(
            acquired=True,
            processed=processed,
            delivered=delivered,
            retried=retried,
            failed=failed,
            coalesced=coalesced,
            timed_out=timed_out,
        )

    def tick(self) -> bool:
        """Request a detached burst only when one indexed due-work probe finds work."""
        return tick(self.store)

    def _deliver_group(self, group: tuple[OutboxItem, ...]) -> tuple[int, int, int]:
        if not self.channels:
            return self._retry_group(group, "unavailable", "no configured delivery channel")
        delivered = retried = failed = 0
        for channel, adapter in self.channels.items():
            for item in group:
                self._mark_dispatching(item, channel)
            representative = group[0]
            try:
                status = _delivery_status(_invoke(adapter, representative))
                if status not in EVIDENCE_RANK or EVIDENCE_RANK[status] < EVIDENCE_RANK[DeliveryStatus.OS_POSTED]:
                    raise RuntimeError("channel returned no delivery evidence")
            except Exception as error:
                retry_result = self._retry_group(group, channel, str(error))
                retried += retry_result[1]
                failed += retry_result[2]
                continue
            if self.after_effect is not None:
                # This hook intentionally sits after the external effect and
                # before durable completion so fault tests exercise that gap.
                self.after_effect(representative, status)
            for item in group:
                self._record_evidence(item, channel, status)
        if not retried and not failed:
            for item in group:
                self._complete(item)
            delivered = len(group)
        return delivered, retried, failed

    def _mark_dispatching(self, item: OutboxItem, channel: str) -> None:
        task_id = _task_id(item)
        if task_id is None:
            raise ValueError("outbox item has no task_id")
        now = utc_now()
        with self.store.transaction(immediate=True) as connection:
            connection.execute("UPDATE outbox SET attempts = attempts + 1 WHERE id = ?", (item.id,))
            connection.execute(
                "INSERT INTO delivery_attempts(task_id, channel, status, attempts, created_at, updated_at, error, idempotency_key) "
                "VALUES (?, ?, ?, 1, ?, ?, NULL, ?) "
                "ON CONFLICT(idempotency_key) DO UPDATE SET status = excluded.status, "
                "attempts = delivery_attempts.attempts + 1, updated_at = excluded.updated_at, error = NULL",
                (task_id, channel, DeliveryStatus.DISPATCHING.value, now, now, _attempt_key(item, channel)),
            )

    def _record_evidence(self, item: OutboxItem, channel: str, status: DeliveryStatus) -> None:
        with self.store.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE delivery_attempts SET status = ?, updated_at = ?, error = NULL "
                "WHERE idempotency_key = ?",
                (status.value, utc_now(), _attempt_key(item, channel)),
            )

    def _retry_group(self, group: tuple[OutboxItem, ...], channel: str, error: str) -> tuple[int, int, int]:
        retried = failed = 0
        for item in group:
            # An unavailable channel did not have a dispatching record yet.
            if channel == "unavailable":
                self._mark_dispatching(item, channel)
            final = self._retry_or_fail(item, channel, error)
            failed += int(final)
            retried += int(not final)
        return 0, retried, failed

    def _retry_or_fail(self, item: OutboxItem, channel: str, error: str) -> bool:
        now = utc_now()
        with self.store.transaction(immediate=True) as connection:
            row = connection.execute("SELECT attempts FROM outbox WHERE id = ?", (item.id,)).fetchone()
            if row is None:
                return True
            attempts = int(row["attempts"])
            terminal = attempts >= self.max_attempts
            status = DeliveryStatus.FAILED if terminal else DeliveryStatus.RETRY_WAIT
            connection.execute(
                "UPDATE delivery_attempts SET status = ?, updated_at = ?, error = ? WHERE idempotency_key = ?",
                (status.value, now, error[:1000], _attempt_key(item, channel)),
            )
            if terminal:
                connection.execute("UPDATE outbox SET completed_at = ? WHERE id = ?", (now, item.id))
            else:
                connection.execute(
                    "UPDATE outbox SET due_at = ? WHERE id = ?", (_retry_due_at(attempts), item.id)
                )
            return terminal

    def _complete(self, item: OutboxItem) -> None:
        with self.store.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE outbox SET completed_at = ? WHERE id = ? AND completed_at IS NULL", (utc_now(), item.id)
            )


def request_dispatch() -> bool:
    """Start a detached dispatcher using a fixed, content-free command line."""
    argv = [sys.executable, "-m", "agent_bridge.cli", "dispatch", "--burst"]
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(argv, **kwargs)
    except OSError:
        return False
    return True


def tick(store: Optional[Store] = None) -> bool:
    """Perform one indexed due-work probe, then request a burst if needed."""
    owned_store = store is None
    if owned_store:
        store = Store.open(get_data_root(os.environ) / "agent-bridge.sqlite3")
    assert store is not None
    try:
        due = store.connection.execute(
            "SELECT 1 FROM outbox WHERE completed_at IS NULL AND due_at <= ? LIMIT 1", (utc_now(),)
        ).fetchone()
        return bool(due) and request_dispatch()
    finally:
        if owned_store:
            store.close()


def _coalesced_groups(items: tuple[OutboxItem, ...]):
    groups = {}
    for item in items:
        payload = item.payload
        key = (payload.get("task_id"), payload.get("recipient"), item.kind)
        groups.setdefault(key, []).append(item)
    return tuple(tuple(group) for group in groups.values())


def _task_id(item: OutboxItem) -> Optional[str]:
    value = item.payload.get("task_id")
    return value if isinstance(value, str) and value else None


def _attempt_key(item: OutboxItem, channel: str) -> str:
    return "{0}:{1}".format(item.idempotency_key, channel)


def _invoke(adapter: DeliveryChannel, item: OutboxItem):
    method = getattr(adapter, "deliver", None)
    return method(item) if callable(method) else adapter(item)  # type: ignore[operator]


def _delivery_status(value: object) -> DeliveryStatus:
    return value if isinstance(value, DeliveryStatus) else DeliveryStatus(str(value))


def _retry_due_at(attempts: int) -> str:
    delay = min(MAX_RETRY_SECONDS, float(2 ** max(0, attempts - 1)))
    jitter = random.uniform(0.0, min(1.0, delay / 4.0))
    return _after_seconds(delay + jitter)


def _after_seconds(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
