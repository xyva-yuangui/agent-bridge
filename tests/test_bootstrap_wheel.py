from __future__ import annotations

import json
import importlib.util
import subprocess
import sys
import unittest
import zipfile

from tests.support import ROOT


class BootstrapWheelTests(unittest.TestCase):
    def test_tracked_offline_wheel_matches_source_and_carries_runtime_scripts(self) -> None:
        checked = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "bootstrap_wheel.py"), "--check"],
            capture_output=True, text=True, encoding="utf-8", errors="strict", timeout=30,
        )
        self.assertEqual(0, checked.returncode, checked.stdout + checked.stderr)
        report = json.loads(checked.stdout)
        self.assertEqual("agent_bridge-2.0.0-py3-none-any.whl", report["wheel"])
        wheel = ROOT / "bootstrap" / report["wheel"]
        with zipfile.ZipFile(wheel) as archive:
            for resource in (
                "agent_bridge/bootstrap/bridge.py",
                "agent_bridge/bootstrap/bridge_mcp.py",
                "agent_bridge/bootstrap/notify_windows.ps1",
                "agent_bridge/native/windows-x86_64/agent-bridge-windows-notify.exe",
            ):
                self.assertIn(resource, archive.namelist())

    @unittest.skipUnless(importlib.util.find_spec("setuptools"), "requires a local wheel build backend")
    def test_rebuilding_bootstrap_wheel_is_byte_reproducible_when_backend_is_available(self) -> None:
        first = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "bootstrap_wheel.py"), "--write"],
            capture_output=True, text=True, encoding="utf-8", errors="strict", timeout=120,
        )
        self.assertEqual(0, first.returncode, first.stdout + first.stderr)
        second = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "bootstrap_wheel.py"), "--write"],
            capture_output=True, text=True, encoding="utf-8", errors="strict", timeout=120,
        )
        self.assertEqual(0, second.returncode, second.stdout + second.stderr)
        self.assertEqual(json.loads(first.stdout)["sha256"], json.loads(second.stdout)["sha256"])


if __name__ == "__main__":
    unittest.main()
