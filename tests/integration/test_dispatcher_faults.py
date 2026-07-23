from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_bridge.dispatcher import Dispatcher
from agent_bridge.models import DeliveryStatus
from agent_bridge.service import BridgeService
from agent_bridge.store import Store


class IdempotentChannel:
    def __init__(self) -> None:
        self.effects: set[str] = set()

    def deliver(self, item):
        self.effects.add(item.idempotency_key)
        return DeliveryStatus.OS_POSTED


class DispatcherFaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store.open(Path(self.directory.name) / "agent-bridge.sqlite3")
        self.service = BridgeService(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.directory.cleanup()

    def test_crash_after_effect_retries_with_the_same_idempotency_key(self) -> None:
        self.service.send_task("sender", "target", "subject", "body")
        channel = IdempotentChannel()
        crashing = Dispatcher(
            self.store,
            {"notification": channel},
            after_effect=lambda item, status: (_ for _ in ()).throw(RuntimeError("crash after effect")),
        )

        with self.assertRaisesRegex(RuntimeError, "crash after effect"):
            crashing.run_burst()
        self.assertIsNone(self.store.scalar("SELECT completed_at FROM outbox"))

        report = Dispatcher(self.store, {"notification": channel}).run_burst()

        self.assertEqual(report.delivered, 1)
        self.assertEqual(len(channel.effects), 1)
        self.assertIsNotNone(self.store.scalar("SELECT completed_at FROM outbox"))

    def test_transient_failure_is_retained_for_bounded_retry(self) -> None:
        self.service.send_task("sender", "target", "subject", "body")

        report = Dispatcher(self.store, {"notification": FailingChannel()}).run_burst()

        self.assertEqual(report.retried, 1)
        self.assertIsNone(self.store.scalar("SELECT completed_at FROM outbox"))
        self.assertGreater(self.store.scalar("SELECT due_at FROM outbox"), "2000-01-01T00:00:00Z")
        self.assertEqual(self.store.scalar("SELECT status FROM delivery_attempts"), DeliveryStatus.RETRY_WAIT.value)


class FailingChannel:
    def deliver(self, item):
        raise OSError("temporary notifier failure")


if __name__ == "__main__":
    unittest.main()
