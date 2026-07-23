from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None

from agent_bridge.adapters import ADAPTER_TYPES
from agent_bridge.adapters.base import TaskCard
from agent_bridge.service import BridgeService
from agent_bridge.store import Store


class HostMcpConsumerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name)
        self.data_root = self.home / ".agent-bridge"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _adapter(self, adapter_type):
        adapter = adapter_type(self.home)
        adapter.marker_path.parent.mkdir(parents=True, exist_ok=True)
        adapter.marker_path.write_text(json.dumps({"host": adapter.name, "mechanisms": [adapter.mechanism]}), encoding="utf-8")
        self.assertTrue(adapter.install().ok)
        return adapter

    def test_each_registered_host_serves_cards_and_durably_acknowledges_once(self) -> None:
        for adapter_type in ADAPTER_TYPES:
            with self.subTest(host=adapter_type.name):
                adapter = self._adapter(adapter_type)
                store = Store.open(self.data_root / "agent-bridge.sqlite3")
                try:
                    task = BridgeService(store).send_task("sender", adapter.name, "subject", "body")
                finally:
                    store.close()
                self.assertEqual(adapter.notify_in_app(TaskCard(task.id, task.subject, task.body)).status.value, "queued")
                card = json.loads(adapter.task_card_path(task.id).read_text(encoding="utf-8"))
                env = os.environ.copy()
                env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
                command = self._registered_command(adapter)
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env,
                )
                try:
                    def call(message):
                        assert process.stdin is not None and process.stdout is not None
                        process.stdin.write(json.dumps(message) + "\n")
                        process.stdin.flush()
                        return json.loads(process.stdout.readline())

                    self.assertEqual(call({"jsonrpc": "2.0", "id": 1, "method": "initialize"})["result"]["serverInfo"]["name"], "agent-bridge-host-consumer")
                    tools = call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
                    self.assertEqual({tool["name"] for tool in tools}, {"list_task_cards", "read_task_card", "acknowledge"})
                    listed = call({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_task_cards", "arguments": {}}})
                    self.assertEqual(listed["result"]["cards"][0]["task_id"], task.id)
                    ack_args = {key: card[key] for key in ("task_id", "integration_version", "protocol_version", "delivery_token")}
                    self.assertTrue(call({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "acknowledge", "arguments": ack_args}})["result"]["acknowledged"])
                    self.assertIn("error", call({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "acknowledge", "arguments": ack_args}}))
                finally:
                    process.terminate()
                    process.communicate(timeout=10)
                verify = Store.open(self.data_root / "agent-bridge.sqlite3")
                try:
                    self.assertEqual(verify.scalar("SELECT COUNT(*) FROM delivery_attempts WHERE task_id = ? AND status = 'agent_acknowledged'", (task.id,)), 1)
                finally:
                    verify.close()

    def test_malformed_json_rpc_returns_parse_error_and_server_continues(self) -> None:
        adapter = self._adapter(ADAPTER_TYPES[0])
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
        command = self._registered_command(adapter)
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env)
        try:
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write("{bad json\n")
            process.stdin.flush()
            self.assertEqual(json.loads(process.stdout.readline())["error"]["code"], -32700)
            process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}) + "\n")
            process.stdin.flush()
            self.assertEqual(json.loads(process.stdout.readline())["result"]["serverInfo"]["name"], "agent-bridge-host-consumer")
        finally:
            process.terminate()
            process.communicate(timeout=10)

    def test_invalid_request_shapes_return_errors_without_terminating_the_server(self) -> None:
        adapter = self._adapter(ADAPTER_TYPES[0])
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
        process = subprocess.Popen(self._registered_command(adapter), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env)
        try:
            assert process.stdin is not None and process.stdout is not None
            for request in (
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": []},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "acknowledge", "arguments": []}},
                {"jsonrpc": "1.0", "id": 3, "method": "initialize"},
            ):
                process.stdin.write(json.dumps(request) + "\n")
                process.stdin.flush()
                self.assertIn("error", json.loads(process.stdout.readline()))
            process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 4, "method": "initialize"}) + "\n")
            process.stdin.flush()
            self.assertEqual(json.loads(process.stdout.readline())["result"]["serverInfo"]["name"], "agent-bridge-host-consumer")
        finally:
            process.terminate()
            process.communicate(timeout=10)

    def test_restarted_consumer_recovers_a_crashed_ack_cleanup_and_rejects_replay(self) -> None:
        adapter = self._adapter(ADAPTER_TYPES[0])
        store = Store.open(self.data_root / "agent-bridge.sqlite3")
        try:
            service = BridgeService(store)
            task = service.send_task("sender", adapter.name, "subject", "body")
            self.assertEqual(adapter.notify_in_app(TaskCard(task.id, task.subject, task.body)).status.value, "queued")
            acknowledgement = adapter.integration_acknowledgement(task.id)
            service.claim_host_acknowledgement(
                acknowledgement.task_id,
                acknowledgement.host_identity,
                acknowledgement.integration_version,
                acknowledgement.protocol_version,
                acknowledgement.delivery_token,
            )
        finally:
            store.close()
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
        process = subprocess.Popen(self._registered_command(adapter), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env)
        try:
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_task_cards", "arguments": {}}}) + "\n")
            process.stdin.flush()
            self.assertEqual(json.loads(process.stdout.readline())["result"]["cards"], [])
            self.assertFalse(adapter.task_card_path(task.id).exists())
            process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "acknowledge", "arguments": {"task_id": task.id}}}) + "\n")
            process.stdin.flush()
            self.assertIn("error", json.loads(process.stdout.readline()))
        finally:
            process.terminate()
            process.communicate(timeout=10)

    def _registered_command(self, adapter):
        if adapter.name == "codex":
            config = tomllib.loads(adapter.config_path.read_text(encoding="utf-8"))
            entry = config["mcp_servers"]["agent_bridge"]
            return [entry["command"], *entry["args"]]
        if adapter.name == "reasonix":
            config = tomllib.loads(adapter.config_path.read_text(encoding="utf-8"))
            entry = next(item for item in config["plugins"] if item["name"] == "agent-bridge")
            return [entry["command"], *entry["args"]]
        config = json.loads(adapter.config_path.read_text(encoding="utf-8"))
        if adapter.name == "claude":
            hook = config["hooks"]["SessionStart"][0]["hooks"][0]
            return [hook["command"], *hook["args"]]
        bundle = Path(config["plugins"]["localPlugins"]["agent-bridge@local"])
        plugin = json.loads((bundle / "plugin.json").read_text(encoding="utf-8"))
        return [plugin["command"], *plugin["args"]]
