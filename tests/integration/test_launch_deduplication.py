from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from agent_bridge.dispatcher import Dispatcher
from agent_bridge.launchers import LaunchDeliveryChannel
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
