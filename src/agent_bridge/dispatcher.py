"""Short-lived, lease-protected transactional outbox dispatcher."""

from __future__ import annotations

import os
import multiprocessing
import random
import sqlite3
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Optional, Tuple

from .delivery import DeferredDelivery, DeliveryChannel, EVIDENCE_RANK
from .models import DeliveryStatus
from .outbox import OutboxItem, due_items, utc_now
from .paths import get_data_root
from .store import Store
from .launchers import LaunchDeliveryChannel


LEASE_NAME = "delivery"
MAX_BURST_SECONDS = 30.0
DEFAULT_LEASE_SECONDS = 35.0
DEFAULT_BATCH_SIZE = 32
DEFAULT_MAX_ATTEMPTS = 5
MAX_RETRY_SECONDS = 300.0
EFFECT_GUARD_SECONDS = 0.02
WORKER_TERMINATE_SECONDS = 0.10
WORKER_KILL_SECONDS = 0.10
STATE_WRITE_RESERVE_SECONDS = 0.10
EFFECT_CLEANUP_RESERVE_SECONDS = (
    WORKER_TERMINATE_SECONDS + WORKER_KILL_SECONDS + STATE_WRITE_RESERVE_SECONDS
)
SPAWN_RESERVE_SECONDS = 0.15


class _DeadlineExceeded(RuntimeError):
    """A dispatcher action cannot safely fit in its remaining burst budget."""


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
        self._lease_lost = False
        self._deadline_reached = False

    def acquire_lease(self, deadline_at: Optional[float] = None) -> bool:
        """Claim the expiring singleton lease without relying on PID liveness."""
        now = utc_now()
        expires_at = _after_seconds(self.lease_seconds)
        try:
            with self._transaction(deadline_at) as connection:
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
        except (_DeadlineExceeded, sqlite3.OperationalError):
            self._deadline_reached = deadline_at is not None
            return False

    def release_lease(self, deadline_at: Optional[float] = None) -> None:
        """Release only this dispatcher's lease; never delete a reclaimed lease."""
        try:
            with self._transaction(deadline_at) as connection:
                connection.execute(
                    "DELETE FROM dispatcher_leases WHERE name = ? AND owner = ?", (LEASE_NAME, self.owner)
                )
        except (_DeadlineExceeded, sqlite3.OperationalError):
            return

    def run_burst(self, deadline_seconds: float = MAX_BURST_SECONDS) -> DispatchReport:
        """Deliver due intents until idle or the hard thirty-second burst limit."""
        duration = min(MAX_BURST_SECONDS, max(0.0, float(deadline_seconds)))
        started = time.monotonic()
        deadline_at = started + duration
        if not self.acquire_lease(deadline_at):
            return DispatchReport(acquired=False)
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
            self.release_lease(deadline_at)
        return DispatchReport(
            acquired=True,
            processed=processed,
            delivered=delivered,
            retried=retried,
            failed=failed,
            coalesced=coalesced,
            timed_out=timed_out,
        )

    @contextmanager
    def _transaction(self, deadline_at: Optional[float]):
        """Run one SQLite write transaction within the remaining burst budget."""
        if deadline_at is not None and _remaining(deadline_at) <= STATE_WRITE_RESERVE_SECONDS:
            raise _DeadlineExceeded()
        previous_timeout = int(self.store.scalar("PRAGMA busy_timeout") or 0)
        timeout_ms = previous_timeout
        if deadline_at is not None:
            timeout_ms = max(1, int((_remaining(deadline_at) - STATE_WRITE_RESERVE_SECONDS) * 1000))
            timeout_ms = min(previous_timeout, timeout_ms)
        self.store.connection.execute("PRAGMA busy_timeout = {0}".format(timeout_ms))
        try:
            with self.store.transaction(immediate=True) as connection:
                yield connection
        finally:
            self.store.connection.execute("PRAGMA busy_timeout = {0}".format(previous_timeout))

    def tick(self) -> bool:
        """Request a detached burst only when one indexed due-work probe finds work."""
        return tick(self.store)

    def _deliver_group(self, group: tuple[OutboxItem, ...], deadline_at: float) -> tuple[int, int, int]:
        if not self.channels:
            return self._retry_group(group, "unavailable", "no configured delivery channel", deadline_at)
        delivered = retried = failed = 0
        representative = group[0]
        for channel, adapter in self.channels.items():
            if not _is_applicable(adapter, representative):
                continue
            for item in group:
                if not self._mark_dispatching(item, channel, deadline_at):
                    self._lease_lost = True
                    return delivered, retried, failed
            self.store.trigger_fault("after_attempt_recorded")
            remaining = deadline_at - time.monotonic()
            if remaining <= EFFECT_CLEANUP_RESERVE_SECONDS + SPAWN_RESERVE_SECONDS:
                self._deadline_reached = True
                return self._retry_group(group, channel, "burst deadline leaves no safe effect budget", deadline_at)
            timeout_seconds = remaining - EFFECT_CLEANUP_RESERVE_SECONDS - SPAWN_RESERVE_SECONDS
            if not self._renew_lease(
                timeout_seconds + EFFECT_CLEANUP_RESERVE_SECONDS + SPAWN_RESERVE_SECONDS, deadline_at
            ):
                self._lease_lost = True
                return delivered, retried, failed
            try:
                completed, result = _invoke_bounded(
                    adapter, representative, representative.idempotency_key, timeout_seconds, deadline_at
                )
                if not completed:
                    self._deadline_reached = True
                    return self._retry_group(group, channel, "channel exceeded bounded timeout", deadline_at)
                if isinstance(result, DeferredDelivery):
                    return self._defer_group(group, channel, result, deadline_at)
                status = _delivery_status(result)
                if status not in EVIDENCE_RANK or EVIDENCE_RANK[status] < EVIDENCE_RANK[DeliveryStatus.OS_POSTED]:
                    raise RuntimeError("channel returned no delivery evidence")
            except Exception as error:
                retry_result = self._retry_group(group, channel, str(error), deadline_at)
                retried += retry_result[1]
                failed += retry_result[2]
                continue
            self.store.trigger_fault(_effect_fault_point(adapter))
            if self.after_effect is not None:
                # This hook intentionally sits after the external effect and
                # before durable completion so fault tests exercise that gap.
                hook_remaining = deadline_at - time.monotonic()
                if hook_remaining <= EFFECT_CLEANUP_RESERVE_SECONDS + SPAWN_RESERVE_SECONDS:
                    self._deadline_reached = True
                    return self._retry_group(group, channel, "burst deadline leaves no safe hook budget", deadline_at)
                hook_completed, ignored = _after_effect_bounded(
                    self.after_effect,
                    representative,
                    status,
                    hook_remaining - EFFECT_CLEANUP_RESERVE_SECONDS - SPAWN_RESERVE_SECONDS,
                    deadline_at,
                )
                if not hook_completed:
                    self._deadline_reached = True
                    return self._retry_group(group, channel, "after-effect hook exceeded bounded timeout", deadline_at)
            for item in group:
                if not self._record_evidence(item, channel, status, deadline_at):
                    self._lease_lost = True
                    return delivered, retried, failed
        if not retried and not failed:
            for item in group:
                self.store.trigger_fault("before_outbox_complete")
                if not self._complete(item, deadline_at):
                    self._lease_lost = True
                    return delivered, retried, failed
            delivered = len(group)
        return delivered, retried, failed

    def _mark_dispatching(self, item: OutboxItem, channel: str, deadline_at: float) -> bool:
        task_id = _task_id(item)
        if task_id is None:
            raise ValueError("outbox item has no task_id")
        now = utc_now()
        try:
            transaction = self._transaction(deadline_at)
            with transaction as connection:
                if not _connection_can_mutate_item(connection, self.owner, item.id):
                    return False
                if connection.execute(
                    "UPDATE outbox SET attempts = attempts + 1 WHERE id = ? AND completed_at IS NULL", (item.id,)
                ).rowcount != 1:
                    return False
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
                return True
        except (_DeadlineExceeded, sqlite3.OperationalError):
            self._deadline_reached = True
            return False

    def _record_evidence(
        self, item: OutboxItem, channel: str, status: DeliveryStatus, deadline_at: float
    ) -> bool:
        try:
            with self._transaction(deadline_at) as connection:
                if not _connection_can_mutate_item(connection, self.owner, item.id):
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
        except (_DeadlineExceeded, sqlite3.OperationalError):
            self._deadline_reached = True
            return False

    def _retry_group(
        self, group: tuple[OutboxItem, ...], channel: str, error: str, deadline_at: float,
        minimum_delay: float = 0.0,
    ) -> tuple[int, int, int]:
        retried = failed = 0
        for item in group:
            # An unavailable channel did not have a dispatching record yet.
            if channel == "unavailable":
                if not self._mark_dispatching(item, channel, deadline_at):
                    self._lease_lost = True
                    continue
            final = self._retry_or_fail(item, channel, error, deadline_at, minimum_delay)
            if final is None:
                self._lease_lost = True
                continue
            failed += int(final)
            retried += int(not final)
        return 0, retried, failed

    def _defer_group(
        self, group: tuple[OutboxItem, ...], channel: str, deferred: DeferredDelivery, deadline_at: float
    ) -> tuple[int, int, int]:
        """Schedule a known-safe pending effect without burning retry attempts."""
        if deferred.due_at <= utc_now():
            raise ValueError("deferred delivery must have a future due time")
        deferred_count = 0
        for item in group:
            if not self._defer(item, channel, deferred, deadline_at):
                self._lease_lost = True
                continue
            deferred_count += 1
        return 0, deferred_count, 0

    def _defer(
        self, item: OutboxItem, channel: str, deferred: DeferredDelivery, deadline_at: float
    ) -> bool:
        try:
            with self._transaction(deadline_at) as connection:
                if not _connection_can_mutate_item(connection, self.owner, item.id):
                    return False
                if connection.execute(
                    "UPDATE outbox SET attempts = CASE WHEN attempts > 0 THEN attempts - 1 ELSE 0 END, due_at = ? "
                    "WHERE id = ? AND completed_at IS NULL",
                    (deferred.due_at, item.id),
                ).rowcount != 1:
                    return False
                connection.execute(
                    "UPDATE delivery_attempts SET status = ?, attempts = CASE WHEN attempts > 0 THEN attempts - 1 ELSE 0 END, "
                    "updated_at = ?, error = ? WHERE idempotency_key = ?",
                    (DeliveryStatus.RETRY_WAIT.value, utc_now(), deferred.reason[:1000], _attempt_key(item, channel)),
                )
                return True
        except (_DeadlineExceeded, sqlite3.OperationalError):
            self._deadline_reached = True
            return False

    def _retry_or_fail(
        self, item: OutboxItem, channel: str, error: str, deadline_at: float,
        minimum_delay: float = 0.0,
    ) -> Optional[bool]:
        now = utc_now()
        try:
            with self._transaction(deadline_at) as connection:
                if not _connection_can_mutate_item(connection, self.owner, item.id):
                    return None
                row = connection.execute(
                "SELECT attempts FROM outbox WHERE id = ? AND completed_at IS NULL", (item.id,)
                ).fetchone()
                if row is None:
                    return None
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
        except (_DeadlineExceeded, sqlite3.OperationalError):
            self._deadline_reached = True
            return None

    def _renew_lease(self, required_seconds: float, deadline_at: float) -> bool:
        """Fence each external effect with a live owner-checked lease renewal."""
        now = utc_now()
        expires_at = _after_seconds(max(self.lease_seconds, required_seconds))
        try:
            with self._transaction(deadline_at) as connection:
                return connection.execute(
                "UPDATE dispatcher_leases SET acquired_at = ?, expires_at = ? "
                "WHERE name = ? AND owner = ? AND expires_at > ?",
                (now, expires_at, LEASE_NAME, self.owner, now),
                ).rowcount == 1
        except (_DeadlineExceeded, sqlite3.OperationalError):
            self._deadline_reached = True
            return False

    def _complete(self, item: OutboxItem, deadline_at: float) -> bool:
        try:
            with self._transaction(deadline_at) as connection:
                if not _connection_can_mutate_item(connection, self.owner, item.id):
                    return False
                connection.execute(
                "UPDATE outbox SET completed_at = ? WHERE id = ? AND completed_at IS NULL", (utc_now(), item.id)
                )
                return True
        except (_DeadlineExceeded, sqlite3.OperationalError):
            self._deadline_reached = True
            return False


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


def _connection_can_mutate_item(connection: Any, owner: str, item_id: int) -> bool:
    row = connection.execute(
        "SELECT 1 FROM dispatcher_leases JOIN outbox ON outbox.id = ? "
        "WHERE dispatcher_leases.name = ? AND dispatcher_leases.owner = ? "
        "AND dispatcher_leases.expires_at > ? AND outbox.completed_at IS NULL",
        (item_id, LEASE_NAME, owner, utc_now()),
    ).fetchone()
    return row is not None


def _attempt_key(item: OutboxItem, channel: str) -> str:
    return "{0}:{1}".format(item.idempotency_key, channel)


def _effect_fault_point(adapter: DeliveryChannel) -> str:
    """Classify durable effects by adapter capability, not registry spelling."""
    if isinstance(adapter, LaunchDeliveryChannel) or getattr(adapter, "effect_kind", None) == "launch":
        return "after_launch_effect"
    return "after_notification_effect"


def _invoke(adapter: DeliveryChannel, item: OutboxItem, idempotency_key: str, timeout_seconds: float):
    method = getattr(adapter, "deliver", None)
    if callable(method):
        return method(item, idempotency_key, timeout_seconds)
    return adapter(item, idempotency_key, timeout_seconds)  # type: ignore[operator]


def _is_applicable(adapter: DeliveryChannel, item: OutboxItem) -> bool:
    method = getattr(adapter, "applicable", None)
    return bool(method(item)) if callable(method) else True


def _invoke_bounded(
    adapter: DeliveryChannel,
    item: OutboxItem,
    idempotency_key: str,
    timeout_seconds: float,
    deadline_at: float,
) -> Tuple[bool, Any]:
    """Run a pickleable adapter in a killable child process.

    ``spawn`` is used everywhere so the contract is equally explicit on
    Windows and POSIX: built-in adapters must be top-level/pickleable and may
    only communicate their result through the worker return value.
    """
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    worker = context.Process(
        target=_adapter_worker,
        args=(adapter, item, idempotency_key, timeout_seconds, deadline_at, sender),
        name="agent-bridge-delivery",
    )
    started = False
    try:
        if _remaining(deadline_at) <= EFFECT_CLEANUP_RESERVE_SECONDS + SPAWN_RESERVE_SECONDS:
            return False, None
        _start_worker(worker, deadline_at)
        started = True
        if _remaining(deadline_at) <= EFFECT_CLEANUP_RESERVE_SECONDS:
            if not _terminate_worker(worker, deadline_at, conclusive=True):
                raise RuntimeError("delivery worker started too late and did not terminate")
            return False, None
        wait_seconds = min(timeout_seconds, _effect_wait_budget(deadline_at))
        if wait_seconds <= 0.0 or not receiver.poll(wait_seconds):
            if not _terminate_worker(worker, deadline_at, conclusive=True):
                raise RuntimeError("delivery worker did not terminate")
            return False, None
        kind, value = receiver.recv()
        worker.join(_remaining(deadline_at))
        if worker.is_alive() and not _terminate_worker(worker, deadline_at, conclusive=True):
            raise RuntimeError("delivery worker did not exit")
        if kind == "error":
            raise RuntimeError(str(value))
        return True, value
    finally:
        receiver.close()
        try:
            sender.close()
        except OSError:
            pass
        if started:
            if worker.is_alive():
                _terminate_worker(worker, deadline_at, conclusive=True)
            worker.join()


def _after_effect_bounded(
    hook: Callable[[OutboxItem, DeliveryStatus], None],
    item: OutboxItem,
    status: DeliveryStatus,
    timeout_seconds: float,
    deadline_at: float,
) -> Tuple[bool, Any]:
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    worker = context.Process(
        target=_after_effect_worker,
        args=(hook, item, status, deadline_at, sender),
        name="agent-bridge-delivery-hook",
    )
    started = False
    try:
        if _remaining(deadline_at) <= EFFECT_CLEANUP_RESERVE_SECONDS + SPAWN_RESERVE_SECONDS:
            return False, None
        _start_worker(worker, deadline_at)
        started = True
        if _remaining(deadline_at) <= EFFECT_CLEANUP_RESERVE_SECONDS:
            if not _terminate_worker(worker, deadline_at, conclusive=True):
                raise RuntimeError("delivery hook worker started too late and did not terminate")
            return False, None
        wait_seconds = min(timeout_seconds, _effect_wait_budget(deadline_at))
        if wait_seconds <= 0.0 or not receiver.poll(wait_seconds):
            if not _terminate_worker(worker, deadline_at, conclusive=True):
                raise RuntimeError("delivery hook worker did not terminate")
            return False, None
        kind, value = receiver.recv()
        worker.join(_remaining(deadline_at))
        if worker.is_alive() and not _terminate_worker(worker, deadline_at, conclusive=True):
            raise RuntimeError("delivery hook worker did not exit")
        if kind == "error":
            raise RuntimeError(str(value))
        return True, value
    finally:
        receiver.close()
        try:
            sender.close()
        except OSError:
            pass
        if started:
            if worker.is_alive():
                _terminate_worker(worker, deadline_at, conclusive=True)
            worker.join()


def _adapter_worker(
    adapter: DeliveryChannel,
    item: OutboxItem,
    idempotency_key: str,
    timeout_seconds: float,
    deadline_at: float,
    sender: Any,
) -> None:
    try:
        if _remaining(deadline_at) <= EFFECT_CLEANUP_RESERVE_SECONDS:
            sender.send(("late", None))
            return
        sender.send(("ok", _invoke(adapter, item, idempotency_key, timeout_seconds)))
    except BaseException as error:
        sender.send(("error", "{0}: {1}".format(type(error).__name__, error)))
    finally:
        sender.close()


def _after_effect_worker(
    hook: Callable[[OutboxItem, DeliveryStatus], None],
    item: OutboxItem,
    status: DeliveryStatus,
    deadline_at: float,
    sender: Any,
) -> None:
    try:
        if _remaining(deadline_at) <= EFFECT_CLEANUP_RESERVE_SECONDS:
            sender.send(("late", None))
            return
        hook(item, status)
        sender.send(("ok", None))
    except BaseException as error:
        sender.send(("error", "{0}: {1}".format(type(error).__name__, error)))
    finally:
        sender.close()


def _terminate_worker(worker: Any, deadline_at: float, conclusive: bool = False) -> bool:
    worker.terminate()
    terminate_wait = WORKER_TERMINATE_SECONDS if conclusive else min(
        WORKER_TERMINATE_SECONDS, _remaining(deadline_at)
    )
    worker.join(terminate_wait)
    if worker.is_alive() and hasattr(worker, "kill"):
        worker.kill()
        if conclusive:
            worker.join()
        else:
            worker.join(min(WORKER_KILL_SECONDS, _remaining(deadline_at)))
    elif worker.is_alive() and conclusive:
        worker.join()
    return not worker.is_alive()


def _start_worker(worker: Any, deadline_at: float) -> None:
    """Start only with enough time left for a child-side late-start guard."""
    if _remaining(deadline_at) <= EFFECT_CLEANUP_RESERVE_SECONDS + SPAWN_RESERVE_SECONDS:
        raise _DeadlineExceeded()
    worker.start()


def _remaining(deadline_at: float) -> float:
    return max(0.0, deadline_at - time.monotonic())


def _effect_wait_budget(deadline_at: float) -> float:
    return max(0.0, _remaining(deadline_at) - EFFECT_CLEANUP_RESERVE_SECONDS)


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
