from __future__ import annotations

import multiprocessing
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_bridge.cli import execute_command
from agent_bridge.delivery import DeliveryChannel
from agent_bridge.dispatcher import Dispatcher
from agent_bridge.models import DeliveryStatus
from agent_bridge.outbox import enqueue, utc_now
from agent_bridge.service import BridgeService
from agent_bridge.store import Store


class RecordingChannel:
    def __init__(self, effects) -> None:
        self.effects = effects

    def deliver(self, item, idempotency_key, timeout_seconds):
        self.asserted_arguments = (idempotency_key, timeout_seconds)
        if item.idempotency_key not in self.effects:
            self.effects.append(item.idempotency_key)
        return DeliveryStatus.OS_POSTED


class DispatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "agent-bridge.sqlite3"
        self.store = Store.open(self.path)
        self.service = BridgeService(self.store)
        self.manager = multiprocessing.Manager()
        self.effects = self.manager.list()
        self.second_store = None

    def tearDown(self) -> None:
        if self.second_store is not None:
            self.second_store.close()
        self.store.close()
        self.manager.shutdown()
        self.directory.cleanup()

    def _recording(self) -> RecordingChannel:
        return RecordingChannel(self.effects)

    def _outbox_item(self):
        task = self.service.send_task("sender", "target", "subject", "body")
        return task, self.store.connection.execute("SELECT * FROM outbox").fetchone()

    def test_only_one_dispatcher_owns_a_live_lease(self) -> None:
        task, _ = self._outbox_item()
        channel = self._recording()
        first = Dispatcher(self.store, {"notification": channel})
        second_store = Store.open(self.path)
        self.second_store = second_store
        second = Dispatcher(second_store, {"notification": channel})

        self.assertTrue(first.acquire_lease())
        report = second.run_burst()

        self.assertFalse(report.acquired)
        self.assertEqual(list(channel.effects), [])
        first.release_lease()

    def test_expired_lease_is_reclaimed(self) -> None:
        task, _ = self._outbox_item()
        channel = self._recording()
        with self.store.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO dispatcher_leases(name, owner, acquired_at, expires_at) VALUES (?, ?, ?, ?)",
                ("delivery", "dead-owner", "2000-01-01T00:00:00Z", "2000-01-01T00:00:00Z"),
            )

        report = Dispatcher(self.store, {"notification": channel}).run_burst()

        self.assertTrue(report.acquired)
        self.assertEqual(list(channel.effects), [self.store.scalar("SELECT idempotency_key FROM outbox")])
        self.assertIsNotNone(self.store.scalar("SELECT completed_at FROM outbox"))

    def test_coalesces_same_task_target_intents_to_one_channel_effect(self) -> None:
        task, row = self._outbox_item()
        with self.store.transaction(immediate=True) as connection:
            enqueue(
                connection,
                "duplicate:" + task.id,
                "task.created",
                {"task_id": task.id, "recipient": "target", "actor": "sender"},
                utc_now(),
            )
        channel = self._recording()

        report = Dispatcher(self.store, {"notification": channel}).run_burst()

        self.assertEqual(report.delivered, 2)
        self.assertEqual(len(channel.effects), 1)
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM outbox WHERE completed_at IS NULL"), 0)

    def test_absent_channel_is_not_recorded_as_delivery(self) -> None:
        self._outbox_item()

        report = Dispatcher(self.store).run_burst()

        self.assertEqual(report.delivered, 0)
        self.assertEqual(self.store.scalar("SELECT completed_at FROM outbox"), None)
        self.assertEqual(self.store.scalar("SELECT status FROM delivery_attempts"), DeliveryStatus.RETRY_WAIT.value)

    def test_burst_honors_an_already_expired_deadline(self) -> None:
        self._outbox_item()
        channel = self._recording()
        started = time.monotonic()

        report = Dispatcher(self.store, {"notification": channel}).run_burst(deadline_seconds=0.0)

        self.assertTrue(report.timed_out)
        self.assertEqual(list(channel.effects), [])
        self.assertLess(time.monotonic() - started, 0.25)

    def test_blocking_adapter_cannot_extend_burst_or_admit_a_competing_effect(self) -> None:
        self._outbox_item()
        blocking = BlockingChannel(self.manager.Event(), self.manager.Event(), self.manager.list())
        dispatcher = Dispatcher(
            self.store,
            {"notification": blocking},
            lease_seconds=1.0,
        )
        started = time.monotonic()

        report = dispatcher.run_burst(deadline_seconds=0.8)

        self.assertTrue(blocking.started.wait(0.5))
        self.assertTrue(report.timed_out)
        self.assertLess(time.monotonic() - started, 0.8)
        second_store = Store.open(self.path)
        self.second_store = second_store
        competing = self._recording()
        second = Dispatcher(second_store, {"notification": competing}, lease_seconds=1.0)
        with self.store.transaction(immediate=True) as connection:
            connection.execute("UPDATE outbox SET due_at = ?", (utc_now(),))
        self.assertTrue(second.run_burst(deadline_seconds=1.0).acquired)
        key = self.store.scalar("SELECT idempotency_key FROM outbox")
        self.assertEqual(list(blocking.keys), [key])
        self.assertEqual(list(competing.effects), [key])
        self.assertFalse(any(
            child.name == "agent-bridge-delivery" and child.is_alive()
            for child in multiprocessing.active_children()
        ))
        blocking.release.set()

    def test_repeated_timeouts_leave_no_delivery_workers(self) -> None:
        self._outbox_item()
        for ignored in range(2):
            blocking = BlockingChannel(self.manager.Event(), self.manager.Event(), self.manager.list())
            report = Dispatcher(self.store, {"notification": blocking}, lease_seconds=1.0).run_burst(0.8)
            self.assertTrue(report.timed_out)
            self.assertTrue(blocking.started.wait(0.5))
            self.assertFalse(any(
                child.name == "agent-bridge-delivery" and child.is_alive()
                for child in multiprocessing.active_children()
            ))
            with self.store.transaction(immediate=True) as connection:
                connection.execute("UPDATE outbox SET due_at = ?", (utc_now(),))

    def test_retry_preserves_existing_channel_evidence(self) -> None:
        self._outbox_item()
        Dispatcher(self.store, {"notification": self._recording()}).run_burst()
        with self.store.transaction(immediate=True) as connection:
            connection.execute("UPDATE outbox SET completed_at = NULL, due_at = ?", (utc_now(),))

        Dispatcher(self.store, {"notification": FailingChannel()}).run_burst()

        row = self.store.connection.execute(
            "SELECT status, error FROM delivery_attempts WHERE channel = 'notification'"
        ).fetchone()
        self.assertEqual(row["status"], DeliveryStatus.OS_POSTED.value)
        self.assertIn("temporary", row["error"])

    def test_dispatch_command_executes_a_real_burst(self) -> None:
        self._outbox_item()

        result = execute_command(self.service, "sender", "dispatch", {"burst": True})

        self.assertTrue(result["dispatch"]["acquired"])
        self.assertEqual(result["dispatch"]["delivered"], 0)
        self.assertEqual(result["dispatch"]["retried"], 1)

    def test_public_commands_tick_without_ticking_the_dispatch_command(self) -> None:
        with patch("agent_bridge.dispatcher.tick", return_value=False) as ticking:
            execute_command(self.service, "sender", "status", {})
            execute_command(self.service, "sender", "dispatch", {"burst": True})

        self.assertEqual(ticking.call_count, 1)

    def test_delivery_mutation_requests_a_detached_burst_after_commit(self) -> None:
        with patch("agent_bridge.dispatcher.tick", return_value=False), patch(
            "agent_bridge.dispatcher.request_dispatch", return_value=True
        ) as requested:
            execute_command(
                self.service,
                "sender",
                "send",
                {"to": "target", "subject": "subject", "body": "body"},
            )

        self.assertEqual(requested.call_count, 1)


if __name__ == "__main__":
    unittest.main()


class BlockingChannel:
    def __init__(self, started, release, keys) -> None:
        self.started = started
        self.release = release
        self.keys = keys

    def deliver(self, item, idempotency_key, timeout_seconds):
        self.keys.append(idempotency_key)
        self.started.set()
        self.release.wait(5.0)
        return DeliveryStatus.OS_POSTED


class FailingChannel:
    def deliver(self, item, idempotency_key, timeout_seconds):
        raise OSError("temporary notifier failure")
