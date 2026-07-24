from __future__ import annotations

import multiprocessing
import tempfile
import unittest
from pathlib import Path

from agent_bridge.dispatcher import Dispatcher
from agent_bridge.models import DeliveryStatus
from agent_bridge.service import BridgeService
from agent_bridge.store import Store


FAULT_POINTS = (
    "before_task_commit",
    "after_task_commit",
    "after_attempt_recorded",
    "after_notification_effect",
    "after_launch_effect",
    "before_outbox_complete",
)


class OneShotFault:
    def __init__(self, point: str) -> None:
        self.point = point
        self.seen: list[str] = []

    def __call__(self, point: str) -> None:
        self.seen.append(point)
        if point == self.point:
            raise RuntimeError("injected fault: " + point)


class DurableIdempotentChannel:
    """A spawn-safe stand-in for an external effect with key-based deduplication."""

    def __init__(self, effects, channel: str = "notification") -> None:
        self.effects = effects
        self.channel = channel

    def deliver(self, item, idempotency_key, timeout_seconds):
        if idempotency_key not in self.effects:
            self.effects.append(idempotency_key)
        return DeliveryStatus.LAUNCH_STARTED if self.channel == "launch" else DeliveryStatus.OS_POSTED


class FaultInjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "agent-bridge.sqlite3"
        self.manager = multiprocessing.Manager()

    def tearDown(self) -> None:
        self.manager.shutdown()
        self.directory.cleanup()

    def test_named_fault_points_are_deterministic_and_recover_without_duplicate_effects(self) -> None:
        for point in FAULT_POINTS:
            with self.subTest(point=point):
                self._assert_restart_recovery(point)

    def _assert_restart_recovery(self, point: str) -> None:
        fault = OneShotFault(point)
        store = Store.open(self.path.with_name(point + ".sqlite3"), fault_hook=fault)
        service = BridgeService(store)
        effects = self.manager.list()
        channel_name = "launch" if point == "after_launch_effect" else "notification"
        channel = DurableIdempotentChannel(effects, channel_name)
        task_id = None
        try:
            if point in ("before_task_commit", "after_task_commit"):
                with self.assertRaisesRegex(RuntimeError, point):
                    service.send_task("codex", "claude", "fault", point)
                if point == "before_task_commit":
                    self.assertEqual(store.scalar("SELECT COUNT(*) FROM tasks"), 0)
                    self.assertEqual(store.scalar("SELECT COUNT(*) FROM outbox"), 0)
                    store.fault_hook = None
                    task_id = service.send_task("codex", "claude", "fault", "recovery").id
                else:
                    self.assertEqual(store.scalar("SELECT COUNT(*) FROM tasks"), 1)
                    self.assertEqual(store.scalar("SELECT COUNT(*) FROM outbox"), 1)
                    task_id = store.scalar("SELECT id FROM tasks")
            else:
                task_id = service.send_task("codex", "claude", "fault", point).id
                with self.assertRaisesRegex(RuntimeError, point):
                    Dispatcher(store, {channel_name: channel}).run_burst()
                self.assertIsNotNone(service.show(str(task_id)))
            self.assertIn(point, fault.seen)
        finally:
            store.close()

        restarted = Store.open(store.path)
        try:
            report = Dispatcher(restarted, {channel_name: channel}).run_burst()
            self.assertTrue(report.acquired)
            self.assertIsNotNone(restarted.scalar("SELECT completed_at FROM outbox"))
            keys = list(effects)
            self.assertEqual(len(keys), len(set(keys)))
            self.assertEqual(restarted.scalar("SELECT COUNT(*) FROM tasks"), 1)
            self.assertEqual(restarted.integrity_report().message.lower(), "ok")
        finally:
            restarted.close()


if __name__ == "__main__":
    unittest.main()
