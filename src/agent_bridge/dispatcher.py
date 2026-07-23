"""Short-lived, lease-protected transactional outbox dispatcher."""

from __future__ import annotations

import os
import random
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Optional, Tuple

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
EFFECT_GUARD_SECONDS = 0.02
EFFECT_FENCE_SECONDS = 1.0


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
        self._retain_lease = False
        self._lease_lost = False
        self._deadline_reached = False

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
        deadline_at = started + duration
        processed = delivered = retried = failed = coalesced = 0
        timed_out = False
        try:
            while True:
                if time.monotonic() >= deadline_at:
                    timed_out = True
                    break
                items = tuple(due_items(self.store.connection, limit=self.batch_size))
                if not items:
                    break
                for group in _coalesced_groups(items):
                    if time.monotonic() >= deadline_at:
                        timed_out = True
                        break
                    processed += len(group)
                    coalesced += len(group) - 1
                    outcome = self._deliver_group(group, deadline_at)
                    delivered += outcome[0]
                    retried += outcome[1]
                    failed += outcome[2]
                    if self._lease_lost or self._deadline_reached:
                        timed_out = self._deadline_reached
                        break
                if timed_out or self._lease_lost:
                    break
        finally:
            if not self._retain_lease:
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

    def _deliver_group(self, group: tuple[OutboxItem, ...], deadline_at: float) -> tuple[int, int, int]:
        if not self.channels:
            return self._retry_group(group, "unavailable", "no configured delivery channel")
        delivered = retried = failed = 0
        for channel, adapter in self.channels.items():
            for item in group:
                self._mark_dispatching(item, channel)
            representative = group[0]
            remaining = deadline_at - time.monotonic()
            if remaining <= EFFECT_GUARD_SECONDS:
                self._deadline_reached = True
                return self._retry_group(group, channel, "burst deadline leaves no safe effect budget")
            timeout_seconds = remaining - EFFECT_GUARD_SECONDS
            if not self._renew_lease(timeout_seconds + EFFECT_GUARD_SECONDS):
                self._lease_lost = True
                return delivered, retried, failed
            try:
                completed, result = _invoke_bounded(
                    adapter, representative, representative.idempotency_key, timeout_seconds
                )
                if not completed:
                    self._fence_timed_out_call()
                    self._deadline_reached = True
                    return self._retry_group(
                        group, channel, "channel exceeded bounded timeout", EFFECT_FENCE_SECONDS + 1.0
                    )
                status = _delivery_status(result)
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
                hook_remaining = deadline_at - time.monotonic()
                if hook_remaining <= EFFECT_GUARD_SECONDS:
                    self._fence_timed_out_call()
                    self._deadline_reached = True
                    return self._retry_group(
                        group, channel, "burst deadline leaves no safe hook budget", EFFECT_FENCE_SECONDS + 1.0
                    )
                hook_completed, ignored = _call_bounded(
                    lambda: self.after_effect(representative, status), hook_remaining - EFFECT_GUARD_SECONDS
                )
                if not hook_completed:
                    self._fence_timed_out_call()
                    self._deadline_reached = True
                    return self._retry_group(
                        group, channel, "after-effect hook exceeded bounded timeout", EFFECT_FENCE_SECONDS + 1.0
                    )
            for item in group:
                if not self._record_evidence(item, channel, status):
                    self._lease_lost = True
                    return delivered, retried, failed
        if not retried and not failed:
            for item in group:
                if not self._complete(item):
                    self._lease_lost = True
                    return delivered, retried, failed
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
                "ON CONFLICT(idempotency_key) DO UPDATE SET status = CASE "
                "WHEN delivery_attempts.status IN ('os_posted', 'plugin_delivered', 'launch_started', "
                "'viewed', 'agent_acknowledged', 'claimed') THEN delivery_attempts.status "
                "ELSE excluded.status END, "
                "attempts = delivery_attempts.attempts + 1, updated_at = excluded.updated_at, error = NULL",
                (task_id, channel, DeliveryStatus.DISPATCHING.value, now, now, _attempt_key(item, channel)),
            )

    def _record_evidence(self, item: OutboxItem, channel: str, status: DeliveryStatus) -> bool:
        with self.store.transaction(immediate=True) as connection:
            if not _connection_has_live_lease(connection, self.owner):
                return False
            row = connection.execute(
                "SELECT status FROM delivery_attempts WHERE idempotency_key = ?", (_attempt_key(item, channel),)
            ).fetchone()
            existing = DeliveryStatus(str(row["status"])) if row is not None else DeliveryStatus.QUEUED
            strongest = status if EVIDENCE_RANK.get(status, -1) >= EVIDENCE_RANK.get(existing, -1) else existing
            connection.execute(
                "UPDATE delivery_attempts SET status = ?, updated_at = ?, error = NULL "
                "WHERE idempotency_key = ?",
                (strongest.value, utc_now(), _attempt_key(item, channel)),
            )
            return True

    def _retry_group(
        self, group: tuple[OutboxItem, ...], channel: str, error: str, minimum_delay: float = 0.0
    ) -> tuple[int, int, int]:
        retried = failed = 0
        for item in group:
            # An unavailable channel did not have a dispatching record yet.
            if channel == "unavailable":
                self._mark_dispatching(item, channel)
            final = self._retry_or_fail(item, channel, error, minimum_delay)
            failed += int(final)
            retried += int(not final)
        return 0, retried, failed

    def _retry_or_fail(self, item: OutboxItem, channel: str, error: str, minimum_delay: float = 0.0) -> bool:
        now = utc_now()
        with self.store.transaction(immediate=True) as connection:
            row = connection.execute("SELECT attempts FROM outbox WHERE id = ?", (item.id,)).fetchone()
            if row is None:
                return True
            attempts = int(row["attempts"])
            terminal = attempts >= self.max_attempts
            status = DeliveryStatus.FAILED if terminal else DeliveryStatus.RETRY_WAIT
            evidence = connection.execute(
                "SELECT status FROM delivery_attempts WHERE idempotency_key = ?", (_attempt_key(item, channel),)
            ).fetchone()
            if evidence is not None:
                existing = DeliveryStatus(str(evidence["status"]))
                if EVIDENCE_RANK.get(existing, -1) >= EVIDENCE_RANK[DeliveryStatus.OS_POSTED]:
                    status = existing
            connection.execute(
                "UPDATE delivery_attempts SET status = ?, updated_at = ?, error = ? WHERE idempotency_key = ?",
                (status.value, now, error[:1000], _attempt_key(item, channel)),
            )
            if terminal:
                connection.execute("UPDATE outbox SET completed_at = ? WHERE id = ?", (now, item.id))
            else:
                connection.execute(
                    "UPDATE outbox SET due_at = ? WHERE id = ?",
                    (_retry_due_at(attempts, minimum_delay), item.id),
                )
            return terminal

    def _renew_lease(self, required_seconds: float) -> bool:
        """Fence each external effect with a live owner-checked lease renewal."""
        now = utc_now()
        expires_at = _after_seconds(max(self.lease_seconds, required_seconds))
        with self.store.transaction(immediate=True) as connection:
            return connection.execute(
                "UPDATE dispatcher_leases SET acquired_at = ?, expires_at = ? "
                "WHERE name = ? AND owner = ? AND expires_at > ?",
                (now, expires_at, LEASE_NAME, self.owner, now),
            ).rowcount == 1

    def _fence_timed_out_call(self) -> None:
        """Keep a timed-out uncooperative call fenced until its retry is due."""
        now = utc_now()
        with self.store.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE dispatcher_leases SET expires_at = ? WHERE name = ? AND owner = ? AND expires_at > ?",
                (_after_seconds(EFFECT_FENCE_SECONDS), LEASE_NAME, self.owner, now),
            )
        self._retain_lease = True

    def _complete(self, item: OutboxItem) -> bool:
        with self.store.transaction(immediate=True) as connection:
            if not _connection_has_live_lease(connection, self.owner):
                return False
            connection.execute(
                "UPDATE outbox SET completed_at = ? WHERE id = ? AND completed_at IS NULL", (utc_now(), item.id)
            )
            return True


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


def _connection_has_live_lease(connection: Any, owner: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM dispatcher_leases WHERE name = ? AND owner = ? AND expires_at > ?",
        (LEASE_NAME, owner, utc_now()),
    ).fetchone()
    return row is not None


def _attempt_key(item: OutboxItem, channel: str) -> str:
    return "{0}:{1}".format(item.idempotency_key, channel)


def _invoke(adapter: DeliveryChannel, item: OutboxItem, idempotency_key: str, timeout_seconds: float):
    method = getattr(adapter, "deliver", None)
    if callable(method):
        return method(item, idempotency_key, timeout_seconds)
    return adapter(item, idempotency_key, timeout_seconds)  # type: ignore[operator]


def _invoke_bounded(
    adapter: DeliveryChannel, item: OutboxItem, idempotency_key: str, timeout_seconds: float
) -> Tuple[bool, Any]:
    """Return on deadline even when a third-party adapter ignores its timeout."""
    return _call_bounded(lambda: _invoke(adapter, item, idempotency_key, timeout_seconds), timeout_seconds)


def _call_bounded(call: Callable[[], Any], timeout_seconds: float) -> Tuple[bool, Any]:
    result = []
    error = []
    done = threading.Event()

    def runner() -> None:
        try:
            result.append(call())
        except BaseException as caught:
            error.append(caught)
        finally:
            done.set()

    thread = threading.Thread(target=runner, name="agent-bridge-delivery", daemon=True)
    thread.start()
    if not done.wait(max(0.0, timeout_seconds)):
        return False, None
    if error:
        raise error[0]
    return True, result[0] if result else None


def _delivery_status(value: object) -> DeliveryStatus:
    return value if isinstance(value, DeliveryStatus) else DeliveryStatus(str(value))


def _retry_due_at(attempts: int, minimum_delay: float = 0.0) -> str:
    delay = max(minimum_delay, min(MAX_RETRY_SECONDS, float(2 ** max(0, attempts - 1))))
    jitter = random.uniform(0.0, min(1.0, delay / 4.0))
    return _after_seconds(delay + jitter)


def _after_seconds(seconds: float) -> str:
    target = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    if target.microsecond:
        target += timedelta(seconds=1)
    return target.replace(microsecond=0).isoformat().replace("+00:00", "Z")
