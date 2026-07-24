from __future__ import annotations

import json
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agent_bridge.cli import build_parser, main
from agent_bridge.path_ownership import PosixPathBackend
from agent_bridge.setup import apply_setup_plan, build_setup_plan, repair, status, uninstall


class SetupLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name) / "用户 [setup]"
        self.home.mkdir()
        self.marker = self.home / ".codex" / "agent-bridge-host.json"
        self.marker.parent.mkdir(parents=True)
        self.marker.write_text(json.dumps({"host": "codex", "mechanisms": ["mcp"]}), encoding="utf-8")
        self.path_backend = PosixPathBackend({})

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_dry_run_plans_detected_host_without_writing(self) -> None:
        plan = build_setup_plan(home=self.home, auto=True)
        report = apply_setup_plan(plan, dry_run=True, path_backend=self.path_backend)
        self.assertEqual(("codex",), report.planned_hosts)
        self.assertFalse((self.home / ".codex" / "config.toml").exists())
        self.assertFalse((self.home / ".profile").exists())
        self.assertFalse((self.home / ".agent-bridge" / "launcher-path-receipt.json").exists())
        self.assertEqual("", self.path_backend.read_current_path())

    def test_install_repair_and_uninstall_are_idempotent_and_conservative(self) -> None:
        original = b"# keep this byte " + "✓".encode("utf-8") + b"\n[other]\nname = '" + "用户".encode("utf-8") + b"'\n"
        config = self.home / ".codex" / "config.toml"
        config.write_bytes(original)
        apply_setup_plan(build_setup_plan(home=self.home, auto=True), path_backend=self.path_backend)
        self.assertIn(b"agent-bridge:codex", config.read_bytes())
        repair(home=self.home, agent="codex", path_backend=self.path_backend)
        report = uninstall(home=self.home, agent="codex", path_backend=self.path_backend)
        self.assertEqual(("codex",), report.removed_hosts)
        self.assertEqual(original, config.read_bytes())
        self.assertFalse((self.home / ".agent-bridge" / "host-integrations" / "codex.json").exists())

    def test_status_reports_degraded_undetected_hosts(self) -> None:
        report = status(home=self.home, path_backend=self.path_backend)
        by_host = {item["host"]: item for item in report["hosts"]}
        self.assertIn("capabilities", by_host["codex"])
        self.assertIn("degradation", by_host["claude"])
        self.assertIn("not discoverable", report["launcher_path"]["degradation"])

    def test_purge_requires_exact_contained_data_root(self) -> None:
        data = self.home / ".agent-bridge" / "data.txt"
        data.parent.mkdir()
        data.write_text("owned", encoding="utf-8")
        report = uninstall(home=self.home, purge_data=True, path_backend=self.path_backend)
        self.assertEqual(str(self.home / ".agent-bridge"), report.purged_data_root)
        self.assertFalse(data.parent.exists())

    def test_uninstall_does_not_rewrite_an_unowned_host_config(self) -> None:
        config = self.home / ".claude" / "settings.json"
        config.parent.mkdir()
        original = b'{"unrelated": true, "keep": [1, 2]}\r\n'
        config.write_bytes(original)
        uninstall(home=self.home, path_backend=self.path_backend)
        self.assertEqual(original, config.read_bytes())

    def test_repair_only_reapplies_an_owned_host_integration(self) -> None:
        report = repair(home=self.home, agent="codex", path_backend=self.path_backend)
        self.assertEqual((), report.applied_hosts)
        self.assertFalse((self.home / ".codex" / "config.toml").exists())

    def test_runtime_failure_rolls_back_owned_runtime(self) -> None:
        with patch("agent_bridge.setup._install_windows_native", side_effect=RuntimeError("native boom")):
            with self.assertRaisesRegex(RuntimeError, "native boom"):
                apply_setup_plan(build_setup_plan(home=self.home, auto=True), path_backend=self.path_backend)
        self.assertFalse((self.home / ".agent-bridge" / "skill").exists())
        self.assertFalse((self.home / ".local" / "bin" / "bridge.cmd").exists())
        self.assertFalse((self.home / ".agent-bridge" / "launcher-path-receipt.json").exists())
        self.assertEqual(b"", (self.home / ".profile").read_bytes())

    def test_setup_plan_records_the_owned_launcher_path_effect(self) -> None:
        plan = build_setup_plan(home=self.home, auto=True)

        path_effects = [effect for effect in plan.effects if effect.target.name == "launcher-path-receipt.json"]

        self.assertEqual(1, len(path_effects))
        self.assertEqual("remove owned launcher PATH entry", path_effects[0].inverse)

    def test_cli_exposes_setup_lifecycle_without_opening_sqlite_service(self) -> None:
        parsed = build_parser().parse_args(["setup", "--auto", "--dry-run"])
        self.assertTrue(parsed.auto)
        self.assertTrue(parsed.dry_run)
        with patch("agent_bridge.cli.open_service", side_effect=AssertionError("setup must not open sqlite")):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(0, main(["--json", "setup", "status"]))


if __name__ == "__main__":
    unittest.main()
