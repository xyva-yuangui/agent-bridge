from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support import ROOT


class BootstrapInstallerTests(unittest.TestCase):
    def test_installers_delegate_setup_with_separate_argv(self) -> None:
        windows = (ROOT / "install.ps1").read_text(encoding="utf-8")
        shell = (ROOT / "install.sh").read_text(encoding="utf-8")
        for source in (windows, shell):
            self.assertIn("pip install", source)
            self.assertIn("agent_bridge.cli", source)
            self.assertIn("setup", source)
        self.assertIn("@bridgeArgs", windows)
        self.assertIn('"${bridge_args[@]}"', shell)

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"), "PowerShell runtime is required")
    def test_windows_bootstrap_installs_a_detected_host_without_admin_rights(self) -> None:
        powershell = shutil.which("powershell.exe")
        assert powershell is not None
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "用户 [bootstrap]"
            marker = home / ".codex" / "agent-bridge-host.json"
            marker.parent.mkdir(parents=True)
            marker.write_text(json.dumps({"host": "codex", "mechanisms": ["mcp"]}), encoding="utf-8")
            environment = os.environ.copy()
            environment.pop("AGENT_BRIDGE_HOME", None)
            completed = subprocess.run(
                [powershell, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "install.ps1"),
                 "-Agent", "codex", "-Python", sys.executable, "-InstallRoot", str(home)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", env=environment, timeout=90,
            )
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            self.assertIn("agent-bridge:codex", (home / ".codex" / "config.toml").read_text(encoding="utf-8"))
            removed = subprocess.run(
                [powershell, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "install.ps1"),
                 "-Agent", "codex", "-Uninstall", "-Python", sys.executable, "-InstallRoot", str(home)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", env=environment, timeout=90,
            )
            self.assertEqual(0, removed.returncode, removed.stdout + removed.stderr)


if __name__ == "__main__":
    unittest.main()
