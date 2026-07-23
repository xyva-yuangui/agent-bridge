from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support import ROOT


WINDOWS_INSTALLER = ROOT / "install.ps1"
POSIX_INSTALLER = ROOT / "install.sh"
WINDOWS_NOTIFY = ROOT / "scripts" / "notify_windows.ps1"


class InstallerContractTests(unittest.TestCase):
    def test_windows_installer_has_complete_cross_app_contract(self):
        source = WINDOWS_INSTALLER.read_text(encoding="utf-8")
        for token in (
            "Resolve-Python",
            "Install-Shared",
            "Register-AgentProfile",
            "Configure-Codex",
            "Configure-Claude",
            "Configure-Reasonix",
            "Configure-ZCode",
            "Uninstall-Agent",
            "[switch]$Auto",
            "[string]$Agent",
            "[string]$As",
            "[string]$Python",
            "[string[]]$WakeArgv",
            "[switch]$Uninstall",
            "bridge_mcp.py",
            "notify_windows.ps1",
            "doctor",
            "--strict",
        ):
            self.assertIn(token, source)
        self.assertNotIn("BurntToast", source)

    def test_posix_installer_has_equivalent_contract(self):
        source = POSIX_INSTALLER.read_text(encoding="utf-8")
        for token in (
            "--auto",
            "--agent",
            "--as",
            "--python",
            "--wake-cmd",
            "--uninstall",
            "configure_codex",
            "configure_claude",
            "configure_reasonix",
            "configure_zcode",
            "bridge_mcp.py",
            "notify_windows.ps1",
            "doctor --strict",
        ):
            self.assertIn(token, source)
        self.assertNotIn(".local\\bin", source)
        self.assertNotIn("C:\\", source)


@unittest.skipUnless(os.name == "nt", "Windows runtime installation test")
class WindowsInstallerRuntimeTests(unittest.TestCase):
    def test_auto_install_and_uninstall_in_isolated_home(self):
        powershell = shutil.which("powershell.exe")
        self.assertIsNotNone(powershell)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install = subprocess.run(
                [
                    powershell,
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(WINDOWS_INSTALLER),
                    "-Auto",
                    "-Python",
                    sys.executable,
                    "-InstallRoot",
                    str(root),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            self.assertEqual(install.returncode, 0, install.stdout + install.stderr)
            skill = root / ".agent-bridge" / "skill"
            self.assertTrue((skill / "scripts" / "bridge.py").is_file())
            self.assertTrue((skill / "scripts" / "bridge_mcp.py").is_file())
            self.assertTrue((skill / "scripts" / "notify_windows.ps1").is_file())
            self.assertTrue((root / ".local" / "bin" / "bridge.cmd").is_file())
            self.assertTrue((root / ".codex" / "config.toml").is_file())
            self.assertTrue((root / ".claude" / "settings.json").is_file())
            self.assertTrue((root / ".reasonix" / "config.toml").is_file())
            self.assertTrue(
                (
                    root
                    / ".zcode"
                    / "cli"
                    / "plugins"
                    / "cache"
                    / "local"
                    / "agent-bridge"
                    / "1.3.0"
                    / "hooks"
                    / "hooks.json"
                ).is_file()
            )
            self.assertTrue((root / ".zcode" / "cli" / "config.json").is_file())
            for agent in ("codex", "claude", "reasonix", "zcode"):
                self.assertTrue(
                    (root / ".agent-bridge" / "agents" / agent / "agent.json").is_file()
                )
            self.assertIn("agent-bridge is ready", install.stdout)

            reinstall = subprocess.run(
                [
                    powershell,
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(WINDOWS_INSTALLER),
                    "-Auto",
                    "-Python",
                    sys.executable,
                    "-InstallRoot",
                    str(root),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            self.assertEqual(
                reinstall.returncode,
                0,
                reinstall.stdout + reinstall.stderr,
            )
            self.assertEqual(
                (root / ".reasonix" / "config.toml")
                .read_text(encoding="utf-8")
                .count("# >>> agent-bridge:reasonix >>>"),
                1,
            )

            uninstall = subprocess.run(
                [
                    powershell,
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(WINDOWS_INSTALLER),
                    "-Auto",
                    "-Uninstall",
                    "-InstallRoot",
                    str(root),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            self.assertEqual(
                uninstall.returncode,
                0,
                uninstall.stdout + uninstall.stderr,
            )
            self.assertFalse((root / ".local" / "bin" / "bridge.cmd").exists())
            self.assertFalse((root / ".agent-bridge" / "skill").exists())
            self.assertFalse(
                (
                    root
                    / ".zcode"
                    / "cli"
                    / "plugins"
                    / "cache"
                    / "local"
                    / "agent-bridge"
                ).exists()
            )
            for config in (
                root / ".codex" / "config.toml",
                root / ".claude" / "settings.json",
                root / ".reasonix" / "config.toml",
                root / ".zcode" / "cli" / "config.json",
            ):
                self.assertNotIn(
                    ".agent-bridge",
                    config.read_text(encoding="utf-8"),
                    str(config),
                )


@unittest.skipUnless(os.name == "nt", "Windows notification runtime test")
class NotificationTests(unittest.TestCase):
    def test_windows_notification_helper_runs_without_optional_modules(self):
        powershell = shutil.which("powershell.exe")
        result = subprocess.run(
            [
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(WINDOWS_NOTIFY),
                "-Title",
                "agent-bridge test",
                "-Message",
                "notification smoke test",
                "-TimeoutMs",
                "1000",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(
            "BurntToast",
            WINDOWS_NOTIFY.read_text(encoding="utf-8"),
        )
