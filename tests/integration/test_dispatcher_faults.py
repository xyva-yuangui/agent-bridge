from __future__ import annotations

import multiprocessing
import tempfile
import unittest
from pathlib import Path

from agent_bridge.dispatcher import Dispatcher
from agent_bridge.models import DeliveryStatus
from agent_bridge.outbox import due_items, utc_now
from agent_bridge.service import BridgeService
from agent_bridge.store import Store


class IdempotentChannel:
    def __init__(self, effects, keys) -> None:
        self.effects = effects
        self.keys = keys

    def deliver(self, item, idempotency_key, timeout_seconds):
        self.keys.append(idempotency_key)
        if idempotency_key not in self.effects:
            self.effects.append(idempotency_key)
        return DeliveryStatus.OS_POSTED


class DispatcherFaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store.open(Path(self.directory.name) / "agent-bridge.sqlite3")
        self.service = BridgeService(self.store)
        self.manager = multiprocessing.Manager()

    def tearDown(self) -> None:
        self.store.close()
        self.manager.shutdown()
        self.directory.cleanup()

    def test_crash_after_effect_retries_with_the_same_idempotency_key(self) -> None:
        self.service.send_task("sender", "target", "subject", "body")
        channel = IdempotentChannel(self.manager.list(), self.manager.list())
        crashing = Dispatcher(
            self.store,
            {"notification": channel},
            after_effect=crash_after_effect,
        )

        with self.assertRaisesRegex(RuntimeError, "crash after effect"):
            crashing.run_burst()
        self.assertIsNone(self.store.scalar("SELECT completed_at FROM outbox"))

        report = Dispatcher(self.store, {"notification": channel}).run_burst()

        self.assertEqual(report.delivered, 1)
        self.assertEqual(len(channel.effects), 1)
        key = self.store.scalar("SELECT idempotency_key FROM outbox")
        self.assertEqual(list(channel.keys), [key, key])
        self.assertIsNotNone(self.store.scalar("SELECT completed_at FROM outbox"))

    def test_transient_failure_is_retained_for_bounded_retry(self) -> None:
        self.service.send_task("sender", "target", "subject", "body")

        report = Dispatcher(self.store, {"notification": FailingChannel()}).run_burst()

        self.assertEqual(report.retried, 1)
        self.assertIsNone(self.store.scalar("SELECT completed_at FROM outbox"))
        self.assertGreater(self.store.scalar("SELECT due_at FROM outbox"), "2000-01-01T00:00:00Z")
        self.assertEqual(self.store.scalar("SELECT status FROM delivery_attempts"), DeliveryStatus.RETRY_WAIT.value)

    def test_stale_owner_cannot_mutate_completed_item(self) -> None:
        self.service.send_task("sender", "target", "subject", "body")
        stale = Dispatcher(self.store, {"notification": FailingChannel()})
        self.assertTrue(stale.acquire_lease())
        item = tuple(due_items(self.store.connection))[0]
        with self.store.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE dispatcher_leases SET owner = ?, expires_at = ? WHERE name = 'delivery'",
                ("new-owner", "2099-01-01T00:00:00Z"),
            )
            connection.execute("UPDATE outbox SET completed_at = ? WHERE id = ?", (utc_now(), item.id))
        before = tuple(self.store.connection.execute(
            "SELECT attempts, due_at, completed_at FROM outbox WHERE id = ?", (item.id,)
        ).fetchone())

        self.assertFalse(stale._mark_dispatching(item, "notification"))
        self.assertIsNone(stale._retry_or_fail(item, "notification", "stale"))

        after = tuple(self.store.connection.execute(
            "SELECT attempts, due_at, completed_at FROM outbox WHERE id = ?", (item.id,)
        ).fetchone())
        self.assertEqual(after, before)
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM delivery_attempts"), 0)


class FailingChannel:
    def deliver(self, item, idempotency_key, timeout_seconds):
        raise OSError("temporary notifier failure")


def crash_after_effect(item, status):
    raise RuntimeError("crash after effect")


if __name__ == "__main__":
    unittest.main()
