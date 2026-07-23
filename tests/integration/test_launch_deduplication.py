from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from agent_bridge.dispatcher import Dispatcher
from agent_bridge.launchers import LaunchDeliveryChannel, launch_stored_agent
from agent_bridge.models import ExecutionPolicy
from agent_bridge.service import BridgeService
from agent_bridge.store import Store


class LaunchDeduplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self.directory.name) / "workspace"
        self.workspace.mkdir()
        self.store = Store.open(Path(self.directory.name) / "agent-bridge.sqlite3")
        self.service = BridgeService(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.directory.cleanup()

    def test_repeated_outbox_work_produces_one_launch(self) -> None:
        marker = Path(self.directory.name) / "launches.txt"
        script = Path(self.directory.name) / "record_launch.py"
        script.write_text(
            "import pathlib, sys\npathlib.Path(sys.argv[1]).write_text('started')\n",
            encoding="utf-8",
        )
        with self.store.transaction(immediate=True) as connection:
            connection.execute("INSERT OR REPLACE INTO projects(id, path) VALUES (?, ?)", ("project", str(self.workspace)))
            connection.execute(
                "INSERT OR REPLACE INTO agents(name, execution_policy, launch_argv_json, workspace_allowlist_json) VALUES (?, ?, ?, ?)",
                ("zcode", ExecutionPolicy.AUTO.value, json.dumps([sys.executable, str(script), str(marker)]), json.dumps([str(self.workspace)])),
            )
        task = self.service.send_task("sender", "zcode", "subject", "body", "project")
        with self.store.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO outbox(idempotency_key, kind, payload_json, due_at) "
                "SELECT ?, kind, payload_json, due_at FROM outbox WHERE idempotency_key = ?",
                ("duplicate:" + task.id, task.id + ":0:task.created"),
            )
        channel = LaunchDeliveryChannel(str(self.store.path))
        report = Dispatcher(self.store, {"launcher": channel}).run_burst()

        self.assertEqual(report.delivered, 2)
        deadline = time.monotonic() + 2.0
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(marker.read_text(encoding="utf-8"), "started")

    def test_direct_wake_reservation_survives_concurrent_callers(self) -> None:
        self._configure_auto_profile()

        class Process:
            pid = 21

        def wake_once():
            store = Store.open(self.store.path)
            try:
                return launch_stored_agent(store, "zcode", self.workspace, "wake:zcode:project")
            finally:
                store.close()

        with patch("agent_bridge.launchers.subprocess.Popen", return_value=Process()) as popen:
            with ThreadPoolExecutor(max_workers=2) as workers:
                results = list(workers.map(lambda ignored: wake_once(), range(2)))

        self.assertEqual(popen.call_count, 1)
        self.assertTrue(all(result.started for result in results))
        self.assertEqual(self.store.scalar("SELECT status FROM launch_reservations"), "started")

    def test_reserved_launch_is_not_restarted_after_popen_before_evidence_crash(self) -> None:
        self._configure_auto_profile()

        class Process:
            pid = 22

        with patch("agent_bridge.launchers.subprocess.Popen", return_value=Process()) as popen, patch(
            "agent_bridge.launchers._record_started", side_effect=RuntimeError("crash after popen")
        ):
            with self.assertRaisesRegex(RuntimeError, "crash after popen"):
                launch_stored_agent(self.store, "zcode", self.workspace, "wake:zcode:project")
        retry = launch_stored_agent(self.store, "zcode", self.workspace, "wake:zcode:project")

        self.assertTrue(retry.started)
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(self.store.scalar("SELECT status FROM launch_reservations"), "reserved")

    def test_manual_recipient_is_not_a_retry_when_auto_channel_is_enabled(self) -> None:
        self._configure_auto_profile()
        task = self.service.send_task("sender", "manual", "manual", "body", "project")
        with self.store.transaction(immediate=True) as connection:
            connection.execute("UPDATE agents SET execution_policy = 'manual' WHERE name = 'manual'")

        report = Dispatcher(self.store, {"launcher": LaunchDeliveryChannel(str(self.store.path))}).run_burst()

        self.assertEqual(report.delivered, 1)
        self.assertEqual(report.retried, 0)
        self.assertIsNotNone(self.store.scalar("SELECT completed_at FROM outbox"))
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM delivery_attempts WHERE task_id = ?", (task.id,)), 0)

    def _configure_auto_profile(self) -> None:
        with self.store.transaction(immediate=True) as connection:
            connection.execute("INSERT OR REPLACE INTO projects(id, path) VALUES (?, ?)", ("project", str(self.workspace)))
            connection.execute(
                "INSERT OR REPLACE INTO agents(name, execution_policy, launch_argv_json, workspace_allowlist_json, cooldown_seconds) VALUES (?, ?, ?, ?, 0)",
                ("zcode", ExecutionPolicy.AUTO.value, json.dumps([sys.executable, "-c", "pass"]), json.dumps([str(self.workspace)])),
            )
