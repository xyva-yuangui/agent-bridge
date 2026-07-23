from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import sqlite3
from pathlib import Path
from typing import Optional
from unittest.mock import patch

from agent_bridge.models import TaskState
from agent_bridge.cli import execute_command
from agent_bridge.service import BridgeService
from agent_bridge.store import Store
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

    def test_project_init_defaults_to_the_current_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_module(
                "agent_bridge.cli", "--json", "project", "init", "--name", "current", home=Path(directory),
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["project"]["path"], str(Path.cwd().resolve()))

    def test_done_files_round_trip_and_clean_removes_delivery_intents(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            sent = run_module(
                "agent_bridge.cli", "--as", "codex", "--json", "send", "--to", "zcode", "--subject", "Files", home=home,
            )
            task_id = json.loads(sent.stdout)["task"]["id"]
            self.assertEqual(run_module("agent_bridge.cli", "--as", "zcode", "claim", task_id, home=home).returncode, 0)
            done = run_module(
                "agent_bridge.cli", "--as", "zcode", "--json", "done", task_id, "--result", "done",
                "--files", "src/a.py, docs/b.md,src/a.py", home=home,
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            shown = run_module("agent_bridge.cli", "--json", "show", task_id, home=home)
            self.assertEqual(json.loads(shown.stdout)["task"]["artifacts"], ["docs/b.md", "src/a.py"])
            cleaned = run_module("agent_bridge.cli", "--json", "clean", "--all", home=home)
            self.assertEqual(cleaned.returncode, 0, cleaned.stderr)
            connection = sqlite3.connect(str(home / "agent-bridge.sqlite3"))
            try:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM outbox").fetchone()[0], 0)
            finally:
                connection.close()

    def test_wake_uses_the_locally_configured_safe_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store.open(Path(directory) / "agent-bridge.sqlite3")
            service = BridgeService(store)
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            try:
                with store.transaction(immediate=True) as connection:
                    connection.execute("INSERT INTO projects(id, path) VALUES (?, ?)", ("project", str(workspace)))
                    connection.execute(
                        "INSERT INTO agents(name, execution_policy, launch_argv_json, workspace_allowlist_json) VALUES (?, 'auto', ?, ?)",
                        ("zcode", json.dumps([sys.executable, "-c", "pass"]), json.dumps([str(workspace)])),
                    )
                with patch("agent_bridge.launchers.subprocess.Popen") as popen:
                    popen.return_value.pid = 42
                    result = execute_command(service, "codex", "wake", {"agent": "zcode", "project": "project"})
            finally:
                store.close()
        self.assertTrue(result["launch"]["started"])
        self.assertEqual(result["launch"]["pid"], 42)
        self.assertFalse(popen.call_args.kwargs["shell"])

    def test_doctor_strict_reports_schema_version_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store.open(Path(directory) / "agent-bridge.sqlite3")
            service = BridgeService(store)
            try:
                with store.transaction(immediate=True) as connection:
                    connection.execute("DELETE FROM schema_migrations")
                report = execute_command(service, "codex", "doctor", {"strict": True})
            finally:
                store.close()
        self.assertFalse(report["ok"])
        self.assertFalse(report["checks"]["schema_version"])

    def test_readmes_use_canonical_delivery_statuses(self):
        for name in ("README.md", "README.zh-CN.md"):
            content = (Path(__file__).parents[2] / name).read_text(encoding="utf-8")
            for obsolete in ("`wake_launched`", "`acknowledged`", "`unavailable`"):
                self.assertNotIn(obsolete, content)

    def test_done_result_is_optional_and_activity_since_is_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            sent = run_module(
                "agent_bridge.cli", "--as", "codex", "--json", "send", "--to", "zcode", "--subject", "Optional", home=home,
            )
            task_id = json.loads(sent.stdout)["task"]["id"]
            self.assertEqual(run_module("agent_bridge.cli", "--as", "zcode", "claim", task_id, home=home).returncode, 0)
            done = run_module("agent_bridge.cli", "--as", "zcode", "--json", "done", task_id, home=home)
            self.assertEqual(done.returncode, 0, done.stderr)
            filtered = run_module(
                "agent_bridge.cli", "--json", "activity", "--since", "1970-01-01T00:00:00Z", home=home,
            )
            self.assertEqual(filtered.returncode, 0, filtered.stderr)
            self.assertGreaterEqual(len(json.loads(filtered.stdout)["events"]), 1)
            invalid = run_module("agent_bridge.cli", "activity", "--since", "not-a-date", home=home)
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn("invalid ISO-8601", invalid.stderr)


if __name__ == "__main__":
    unittest.main()
