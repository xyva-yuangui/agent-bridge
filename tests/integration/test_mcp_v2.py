from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List

from tests.integration.test_cli_v2 import REQUIRED_COMMANDS
from tests.support import MCP_PATH


EXCLUDED_MCP_COMMANDS = {"dispatch", "tui", "setup", "uninstall", "open-action"}


class McpV2Tests(unittest.TestCase):
    def exchange(self, home: Path, requests: List[Dict]) -> List[Dict]:
        environment = os.environ.copy()
        environment["AGENT_BRIDGE_HOME"] = str(home)
        payload = "".join(json.dumps(request) + "\n" for request in requests)
        result = subprocess.run(
            [sys.executable, str(MCP_PATH), "--as", "codex"], input=payload,
            capture_output=True, text=True, encoding="utf-8", errors="strict",
            env=environment, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return [json.loads(line) for line in result.stdout.splitlines() if line]

    def test_tools_match_noninteractive_cli_and_execute_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            responses = self.exchange(home, [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "bridge_whoami", "arguments": {}}},
                {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "bridge_send", "arguments": {"to": "zcode", "subject": "Review", "body": "Please review"}}},
            ])
            names = {tool["name"].removeprefix("bridge_").replace("_", "-") for tool in responses[1]["result"]["tools"]}
            self.assertEqual(names, REQUIRED_COMMANDS - EXCLUDED_MCP_COMMANDS)
            self.assertIn("codex", responses[2]["result"]["content"][0]["text"])
            task = json.loads(responses[3]["result"]["content"][0]["text"])["task"]

            workflow = self.exchange(home, [
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "bridge_show", "arguments": {"task_id": task["id"]}}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "bridge_claim", "arguments": {"task_id": task["id"], "actor": "zcode"}}},
            ])
            self.assertEqual(json.loads(workflow[0]["result"]["content"][0]["text"])["task"]["id"], task["id"])
            self.assertEqual(json.loads(workflow[1]["result"]["content"][0]["text"])["task"]["state"], "working")

    def test_missing_as_value_is_a_parse_error_not_index_error(self):
        result = subprocess.run(
            [sys.executable, "-m", "agent_bridge.mcp", "--as"], capture_output=True,
            text=True, encoding="utf-8", errors="strict", timeout=30,
        )
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("IndexError", result.stderr)

    def test_mcp_validates_malformed_requests_and_continues(self):
        with tempfile.TemporaryDirectory() as directory:
            responses = self.exchange(Path(directory), [
                [],
                3,
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": []},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "bridge_whoami", "arguments": {}}},
            ])
        self.assertEqual([response["error"]["code"] for response in responses[:3]], [-32600, -32600, -32602])
        self.assertIn("codex", responses[3]["result"]["content"][0]["text"])

    def test_mcp_advertises_and_enforces_tool_schemas(self):
        with tempfile.TemporaryDirectory() as directory:
            responses = self.exchange(Path(directory), [
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "bridge_send", "arguments": {"subject": "Missing target"}}},
            ])
        tools = {tool["name"]: tool for tool in responses[0]["result"]["tools"]}
        schema = tools["bridge_send"]["inputSchema"]
        self.assertEqual(schema["required"], ["to", "subject"])
        self.assertEqual(schema["properties"]["to"]["type"], "string")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(responses[1]["error"]["code"], -32602)

    def test_mcp_project_init_default_and_wake_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            responses = self.exchange(Path(directory), [
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "bridge_project", "arguments": {"action": "init", "name": "current"}}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "bridge_wake", "arguments": {"agent": "zcode"}}},
            ])
        self.assertIn("project", responses[0]["result"]["content"][0]["text"])
        self.assertTrue(responses[1]["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
