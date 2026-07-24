from __future__ import annotations

import multiprocessing
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from agent_bridge.dispatcher import Dispatcher
from agent_bridge.launchers import LaunchDeliveryChannel
from agent_bridge.models import DeliveryStatus
from agent_bridge.outbox import utc_now
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
        self.effect_kind = "launch" if channel == "launch" else "notification"

    def deliver(self, item, idempotency_key, timeout_seconds):
        if idempotency_key not in self.effects:
            self.effects.append(idempotency_key)
        return DeliveryStatus.LAUNCH_STARTED if self.channel == "launch" else DeliveryStatus.OS_POSTED


class FsyncEffectChannel:
    """A pickleable external-effect sink that survives an abrupt dispatcher exit."""

    def __init__(self, sink_path: str, effect_kind: str) -> None:
        self.sink_path = sink_path
        self.effect_kind = effect_kind

    def deliver(self, item, idempotency_key, timeout_seconds):
        path = Path(self.sink_path)
        existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        if idempotency_key not in existing:
            with path.open("a", encoding="utf-8") as sink:
                sink.write(idempotency_key + "\n")
                sink.flush()
                os.fsync(sink.fileno())
        return DeliveryStatus.LAUNCH_STARTED if self.effect_kind == "launch" else DeliveryStatus.OS_POSTED


class HardExitFault:
    def __init__(self, point: str) -> None:
        self.point = point

    def __call__(self, point: str) -> None:
        if point == self.point:
            os._exit(73)


def _hard_crash_dispatcher(database_path: str, sink_path: str, point: str, effect_kind: str) -> None:
    store = Store.open(Path(database_path), fault_hook=HardExitFault(point))
    channel_name = "launcher" if effect_kind == "launch" else "notification"
    Dispatcher(store, {channel_name: FsyncEffectChannel(sink_path, effect_kind)}, lease_seconds=0.05).run_burst(
        deadline_seconds=2.0
    )


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

    def test_cli_registered_launcher_channel_uses_the_launch_effect_fault_point(self) -> None:
        fault = OneShotFault("after_launch_effect")
        store = Store.open(self.path.with_name("typed-launch.sqlite3"), fault_hook=fault)
        try:
            service = BridgeService(store)
            task = service.send_task("codex", "claude", "launch", "body")
            argv = [sys.executable, "-c", "pass"]
            if os.name == "nt":
                argv = [os.environ["COMSPEC"], "/d", "/c", "exit 0"]
            with store.transaction(immediate=True) as connection:
                connection.execute("UPDATE projects SET path = ? WHERE id = ?", (str(Path.cwd()), "default"))
                connection.execute(
                    "UPDATE agents SET execution_policy = 'auto', launch_argv_json = ?, workspace_allowlist_json = ? WHERE name = 'claude'",
                    (json.dumps(argv), json.dumps([str(Path.cwd())])),
                )
            channel = LaunchDeliveryChannel(str(store.path))
            with self.assertRaisesRegex(RuntimeError, "after_launch_effect"):
                Dispatcher(store, {"launcher": channel}).run_burst()
            self.assertIn("after_launch_effect", fault.seen)
            self.assertNotIn("after_notification_effect", fault.seen)
            self.assertIsNone(store.scalar("SELECT completed_at FROM outbox WHERE idempotency_key LIKE ?", (task.id + ":%",)))
        finally:
            store.close()

    def test_hard_crash_after_each_effect_preserves_lease_then_recovers_without_duplicate_effect(self) -> None:
        for effect_kind, point in (("notification", "after_notification_effect"), ("launch", "after_launch_effect")):
            with self.subTest(effect_kind=effect_kind):
                self._assert_hard_crash_recovery(effect_kind, point)

    def _assert_hard_crash_recovery(self, effect_kind: str, point: str) -> None:
        database = self.path.with_name("hard-crash-{0}.sqlite3".format(effect_kind))
        sink = database.with_suffix(".effects")
        store = Store.open(database)
        try:
            task = BridgeService(store).send_task("codex", "claude", "hard crash", effect_kind)
            context = multiprocessing.get_context("spawn")
            child = context.Process(target=_hard_crash_dispatcher, args=(str(database), str(sink), point, effect_kind))
            child.start()
            child.join(15)
            self.assertEqual(child.exitcode, 73)
            self.assertEqual(sink.read_text(encoding="utf-8").splitlines(), [
                store.scalar("SELECT idempotency_key FROM outbox WHERE id = 1")
            ])

            channel_name = "launcher" if effect_kind == "launch" else "notification"
            channel = FsyncEffectChannel(str(sink), effect_kind)
            self.assertFalse(Dispatcher(store, {channel_name: channel}, lease_seconds=0.05).run_burst().acquired)
            self.assertEqual(len(sink.read_text(encoding="utf-8").splitlines()), 1)
            self._wait_for(lambda: str(store.scalar(
                "SELECT expires_at <= ? FROM dispatcher_leases WHERE name = 'delivery'", (utc_now(),)
            )) == "1")

            restarted = Store.open(database)
            try:
                report = Dispatcher(restarted, {channel_name: channel}, lease_seconds=0.05).run_burst()
                self.assertTrue(report.acquired)
                self.assertIsNotNone(restarted.scalar("SELECT completed_at FROM outbox WHERE id = 1"))
                key = restarted.scalar("SELECT idempotency_key FROM outbox WHERE id = 1")
                self.assertEqual(sink.read_text(encoding="utf-8").splitlines(), [key])
                self.assertEqual(restarted.scalar("SELECT COUNT(*) FROM delivery_attempts WHERE idempotency_key = ?", (key + ":" + channel_name,)), 1)
                self.assertGreaterEqual(restarted.scalar("SELECT attempts FROM delivery_attempts WHERE idempotency_key = ?", (key + ":" + channel_name,)), 2)
                self.assertEqual(BridgeService(restarted).show(task.id).id, task.id)
            finally:
                restarted.close()
        finally:
            store.close()

    @staticmethod
    def _wait_for(condition) -> None:
        deadline = time.monotonic() + 5.0
        while not condition():
            if time.monotonic() >= deadline:
                raise AssertionError("controlled dispatcher lease did not expire")
            time.sleep(0.01)

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
