"""Exercise one normal installer invocation from an installed wheel, not the checkout."""

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


@unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"), "requires PowerShell")
class PackageOnlyWindowsInstallerTests(unittest.TestCase):
    def test_one_normal_install_uses_site_package_with_no_pythonpath(self) -> None:
        powershell = shutil.which("powershell.exe")
        assert powershell is not None
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            home = temporary_root / "home"
            user_base = temporary_root / "user-base"
            (home / ".codex").mkdir(parents=True)
            (home / ".codex" / "agent-bridge-host.json").write_text(
                json.dumps({"host": "codex", "mechanisms": ["mcp"]}), encoding="utf-8"
            )
            environment = os.environ.copy()
            environment.update({"PYTHONUSERBASE": str(user_base), "PIP_DISABLE_PIP_VERSION_CHECK": "1"})
            environment.pop("PYTHONPATH", None)
            installed = subprocess.run(
                [powershell, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "install.ps1"),
                 "-Agent", "codex", "-Python", sys.executable, "-InstallRoot", str(home)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", env=environment, timeout=120,
            )
            self.assertEqual(0, installed.returncode, installed.stdout + installed.stderr)
            self.assertNotIn("DEGRADED development fallback", installed.stdout + installed.stderr)
            probe = subprocess.run(
                [sys.executable, "-c", "import agent_bridge, json; print(json.dumps({'origin': agent_bridge.__file__}))"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", env=environment, timeout=30,
            )
            self.assertEqual(0, probe.returncode, probe.stdout + probe.stderr)
            origin = Path(json.loads(probe.stdout)["origin"]).resolve()
            self.assertIn(user_base.resolve(), origin.parents)
            self.assertNotIn((ROOT / "src").resolve(), origin.parents)
            receipt = json.loads((home / ".agent-bridge" / "host-integrations" / "codex.json").read_text(encoding="utf-8"))
            started = subprocess.run(
                receipt["entrypoint"], input='{"jsonrpc":"2.0","id":1,"method":"initialize"}\n',
                capture_output=True, text=True, encoding="utf-8", errors="replace", env=environment, timeout=30,
            )
            self.assertEqual(0, started.returncode, started.stdout + started.stderr)
            self.assertIn('"serverInfo"', started.stdout)


if __name__ == "__main__":
    unittest.main()
