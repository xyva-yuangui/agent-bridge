from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support import MCP_PATH, write_agent


class McpParityTests(unittest.TestCase):
    def exchange(self, home: Path, requests: list[dict]) -> list[dict]:
        env = os.environ.copy()
        env["AGENT_BRIDGE_HOME"] = str(home)
        env["AGENT_BRIDGE_DISABLE_NOTIFY"] = "1"
        env["PYTHONIOENCODING"] = "gbk"
        payload = "".join(json.dumps(item) + "\n" for item in requests)
        completed = subprocess.run(
            [sys.executable, str(MCP_PATH), "--as", "codex"],
            input=payload,
            capture_output=True,
            text=True,
            encoding="gbk",
            errors="strict",
            env=env,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return [json.loads(line) for line in completed.stdout.splitlines() if line]

    def test_mcp_exposes_every_cli_workflow_and_matching_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write_agent(home, "codex")
            responses = self.exchange(
                home,
                [
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {},
                    },
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/list",
                        "params": {},
                    },
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {
                            "name": "bridge_whoami",
                            "arguments": {},
                        },
                    },
                ],
            )

        self.assertEqual(
            responses[0]["result"]["serverInfo"]["version"],
            "2.0.0",
        )
        tool_names = {
            tool["name"] for tool in responses[1]["result"]["tools"]
        }
        self.assertTrue(
            {
                "bridge_status",
                "bridge_inbox",
                "bridge_send",
                "bridge_claim",
                "bridge_done",
                "bridge_show",
                "bridge_board",
                "bridge_question",
                "bridge_answer",
                "bridge_review",
                "bridge_wake",
                "bridge_agents",
                "bridge_activity",
                "bridge_context",
                "bridge_clean",
                "bridge_doctor",
                "bridge_project",
                "bridge_whoami",
                "bridge_who_coordinates",
                "bridge_log",
            }.issubset(tool_names)
        )
        self.assertFalse(responses[2]["result"]["isError"])
        self.assertIn("codex", responses[2]["result"]["content"][0]["text"])

