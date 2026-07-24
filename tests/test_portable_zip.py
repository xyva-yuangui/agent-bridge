from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

from tests.support import ROOT


spec = importlib.util.spec_from_file_location("build_portable_zip", ROOT / "scripts" / "build_portable_zip.py")
assert spec and spec.loader
portable = importlib.util.module_from_spec(spec)
spec.loader.exec_module(portable)


class PortableZipTests(unittest.TestCase):
    def _fake_macos_app(self, root: Path) -> Path:
        app = root / "AgentBridgeNotifier.app" / "Contents" / "MacOS"
        app.mkdir(parents=True)
        (app / "AgentBridgeNotifier").write_bytes(b"universal2-test-helper")
        return app.parents[1]

    def test_cross_platform_zip_is_deterministic_and_has_exact_release_components(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, second = root / "first.zip", root / "second.zip"
            app = self._fake_macos_app(root)
            helper = ROOT / "src" / "agent_bridge" / "native" / "windows-x86_64" / "agent-bridge-windows-notify.exe"
            portable.build(first, ROOT, "2.0.0", helper, app)
            portable.build(second, ROOT, "2.0.0", helper, app)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            portable.check(first, "2.0.0")
            with zipfile.ZipFile(first) as archive:
                names = set(archive.namelist())
            prefix = "agent-bridge-2.0.0/"
            for name in (
                "bootstrap/agent_bridge-2.0.0-py3-none-any.whl", "install.ps1", "install.sh", "LICENSE",
                "README.md", "README.zh-CN.md", "inventory.json", "SHA256SUMS.txt",
                "native/windows-x86_64/agent-bridge-windows-notify.exe",
                "native/macos-universal2/AgentBridgeNotifier.app/Contents/MacOS/AgentBridgeNotifier",
                "integrations/codex/manifest.json", "integrations/claude/manifest.json",
                "integrations/reasonix/manifest.json", "integrations/zcode/manifest.json",
            ):
                self.assertIn(prefix + name, names)
            self.assertFalse(any(name.endswith(".zip") for name in names))

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"), "requires PowerShell")
    def test_extracted_zip_under_cjk_path_installs_from_bundled_wheel_without_checkout(self) -> None:
        powershell = shutil.which("powershell.exe")
        assert powershell is not None
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "portable.zip"
            portable.build(
                archive, ROOT, "2.0.0",
                ROOT / "src" / "agent_bridge" / "native" / "windows-x86_64" / "agent-bridge-windows-notify.exe",
                self._fake_macos_app(root),
            )
            extracted = root / "提取 空格"
            with zipfile.ZipFile(archive) as contents:
                contents.extractall(extracted)
            package_root = extracted / "agent-bridge-2.0.0"
            home, user_base = root / "home", root / "wheel-user"
            (home / ".codex").mkdir(parents=True)
            (home / ".codex" / "agent-bridge-host.json").write_text(json.dumps({"host": "codex", "mechanisms": ["mcp"]}), encoding="utf-8")
            environment = os.environ.copy()
            environment.update({"PYTHONUSERBASE": str(user_base), "PIP_DISABLE_PIP_VERSION_CHECK": "1"})
            environment.pop("PYTHONPATH", None)
            installed = subprocess.run(
                [powershell, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(package_root / "install.ps1"), "-Agent", "codex", "-Python", sys.executable, "-InstallRoot", str(home)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", env=environment, cwd=root, timeout=120,
            )
            self.assertEqual(0, installed.returncode, installed.stdout + installed.stderr)
            self.assertNotIn("DEGRADED", installed.stdout + installed.stderr)
            self.assertTrue((home / ".agent-bridge" / "native" / "agent-bridge-windows-notify.exe").is_file())


if __name__ == "__main__":
    unittest.main()
