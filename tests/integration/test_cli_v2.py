from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional

from agent_bridge.models import TaskState
from tests.support import BRIDGE_PATH


REQUIRED_COMMANDS = {
    "status", "inbox", "send", "claim", "done", "show", "board",
    "question", "answer", "review", "wake", "agents", "activity",
    "context", "clean", "doctor", "project", "whoami",
    "who-coordinates", "log", "dispatch", "tui", "setup",
    "uninstall", "migrate", "export", "open-action",
}


def run_module(module: str, *arguments: str, home: Optional[Path] = None):
    environment = os.environ.copy()
    if home is not None:
        environment["AGENT_BRIDGE_HOME"] = str(home)
    return subprocess.run(
        [sys.executable, "-m", module, *arguments], capture_output=True, text=True,
        encoding="utf-8", errors="strict", env=environment, timeout=30,
    )


class CliV2Tests(unittest.TestCase):
    def test_help_exposes_required_commands(self):
        result = run_module("agent_bridge.cli", "--help")

        self.assertEqual(result.returncode, 0, result.stderr)
        for command in REQUIRED_COMMANDS:
            self.assertIn(command, result.stdout)

    def test_json_workflow_uses_service_views(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            sent = run_module(
                "agent_bridge.cli", "--as", "codex", "--json", "send", "--to", "zcode",
                "--subject", "Review", "--body", "Please review", home=home,
            )
            self.assertEqual(sent.returncode, 0, sent.stderr)
            task = json.loads(sent.stdout)["task"]

            claimed = run_module(
                "agent_bridge.cli", "--as", "zcode", "--json", "claim", task["id"], home=home,
            )
            self.assertEqual(claimed.returncode, 0, claimed.stderr)
            self.assertEqual(json.loads(claimed.stdout)["task"]["state"], "working")

            shown = run_module(
                "agent_bridge.cli", "--as", "codex", "--json", "show", task["id"], home=home,
            )
            self.assertEqual(shown.returncode, 0, shown.stderr)
            self.assertEqual(json.loads(shown.stdout)["task"]["subject"], "Review")

    def test_migrate_and_export_call_v2_store_functions(self):
        fixture = Path(__file__).parents[1] / "fixtures" / "v1"
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            migrated = run_module(
                "agent_bridge.cli", "--json", "migrate", str(fixture), home=home,
            )
            self.assertEqual(migrated.returncode, 0, migrated.stderr)
            self.assertEqual(json.loads(migrated.stdout)["imported_tasks"], 1)
            destination = home / "snapshot.json"
            exported = run_module(
                "agent_bridge.cli", "--json", "export", str(destination), home=home,
            )
            self.assertEqual(exported.returncode, 0, exported.stderr)
            self.assertEqual(json.loads(destination.read_text(encoding="utf-8"))["tasks"][0]["id"], "task-1")

    def test_skill_documentation_uses_canonical_task_states(self):
        documented = set()
        for line in (Path(__file__).parents[2] / "SKILL.md").read_text(encoding="utf-8").splitlines():
            if "->" in line:
                documented.update(part.strip() for part in line.split("->"))
        self.assertEqual(documented, {state.value for state in TaskState})


if __name__ == "__main__":
    unittest.main()
