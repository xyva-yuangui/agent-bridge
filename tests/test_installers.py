from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support import ROOT

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9/3.10 runtime compatibility
    tomllib = None


WINDOWS_INSTALLER = ROOT / "install.ps1"
POSIX_INSTALLER = ROOT / "install.sh"
WINDOWS_NOTIFY = ROOT / "scripts" / "notify_windows.ps1"


class InstallerContractTests(unittest.TestCase):
    def test_windows_installer_is_a_safe_setup_bootstrap(self):
        source = WINDOWS_INSTALLER.read_text(encoding="utf-8")
        for token in (
            "Resolve-Python",
            "[switch]$Auto",
            "[string]$Agent",
            "[string]$As",
            "[string]$Python",
            "[string[]]$WakeArgv",
            "[switch]$Uninstall",
            "pip install",
            "agent_bridge.cli",
            "--home",
            "@bridgeArgs",
            "-LiteralPath",
        ):
            self.assertIn(token, source)
        self.assertNotIn("BurntToast", source)

    def test_posix_installer_is_a_safe_setup_bootstrap(self):
        source = POSIX_INSTALLER.read_text(encoding="utf-8")
        for token in (
            "--auto",
            "--agent",
            "--as",
            "--python",
            "--wake-cmd",
            "--uninstall",
            "pip install",
            "agent_bridge.cli",
            "--home",
            '"${bridge_args[@]}"',
        ):
            self.assertIn(token, source)
        self.assertNotIn(".local\\bin", source)
        self.assertNotIn("C:\\", source)


@unittest.skipUnless(os.name == "nt", "Windows runtime installation test")
class WindowsInstallerRuntimeTests(unittest.TestCase):
    @staticmethod
    def isolated_subprocess_env():
        environment = os.environ.copy()
        for name in tuple(environment):
            if name.upper().startswith("AGENT_BRIDGE_") or name.upper() in (
                "PYTHONHOME",
                "PYTHONPATH",
            ):
                environment.pop(name, None)
        return environment

    def test_auto_install_and_uninstall_in_isolated_home(self):
        powershell = shutil.which("powershell.exe")
        self.assertIsNotNone(powershell)
        isolated_env = self.isolated_subprocess_env()
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as environment_key:
                prior_user_helper = winreg.QueryValueEx(
                    environment_key, "AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER"
                )[0]
        except FileNotFoundError:
            prior_user_helper = ""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text("", encoding="utf-8")
            (root / ".codex").mkdir(); (root / ".codex" / "config.toml").write_text("", encoding="utf-8")
            (root / ".claude").mkdir(); (root / ".claude" / "settings.json").write_text("{}\n", encoding="utf-8")
            (root / ".reasonix").mkdir(); (root / ".reasonix" / "config.toml").write_text("", encoding="utf-8")
            (root / ".zcode" / "cli").mkdir(parents=True); (root / ".zcode" / "cli" / "config.json").write_text("{}\n", encoding="utf-8")
            stale_profile = root / ".agent-bridge" / "agents" / "old-test" / "agent.json"
            stale_profile.parent.mkdir(parents=True)
            stale_profile.write_text(
                '{"name":"old-test","last_seen":"2000-01-01T00:00:00Z"}',
                encoding="utf-8",
            )
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
                    "-DevSourceFallback",
                    "-Python",
                    sys.executable,
                    "-InstallRoot",
                    str(root),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=isolated_env,
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
            self.assertIn("OK", install.stdout)
            runtime = skill / "runtime" / "agent_bridge"
            self.assertTrue((runtime / "cli.py").is_file())
            installed_cli = subprocess.run(
                [
                    sys.executable,
                    str(skill / "scripts" / "bridge.py"),
                    "--data-root",
                    str(root / ".agent-bridge"),
                    "--json",
                    "whoami",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=isolated_env,
                timeout=30,
            )
            self.assertEqual(
                installed_cli.returncode,
                0,
                installed_cli.stdout + installed_cli.stderr,
            )
            notifier = root / ".agent-bridge" / "native"
            helper = notifier / "agent-bridge-windows-notify.exe"
            receipt = json.loads((notifier / "receipt.json").read_text(encoding="utf-8-sig"))
            self.assertEqual(
                set(receipt),
                {"schema", "owner", "helper_path", "sha256", "activation_argv"},
            )
            self.assertEqual(receipt["schema"], 1)
            self.assertEqual(receipt["owner"], "agent-bridge.windows-notify")
            self.assertTrue(os.path.samefile(receipt["helper_path"], helper))
            self.assertEqual(
                receipt["sha256"].lower(),
                hashlib.sha256(helper.read_bytes()).hexdigest(),
            )
            activation_argv = receipt["activation_argv"]
            self.assertEqual(
                activation_argv[2:],
                ["--as", "notification-action", "--data-root", activation_argv[5]],
            )
            self.assertTrue(os.path.samefile(activation_argv[0], sys.executable))
            self.assertTrue(
                os.path.samefile(activation_argv[1], skill / "scripts" / "bridge.py")
            )
            self.assertTrue(
                os.path.samefile(activation_argv[5], root / ".agent-bridge")
            )
            status = subprocess.run(
                [str(helper)],
                input='{"operation":"status"}',
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=isolated_env,
                timeout=15,
            )
            self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
            self.assertTrue(json.loads(status.stdout)["ok"])
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Classes\agent-bridge",
            ) as protocol_key:
                activation_argv = json.loads(
                    winreg.QueryValueEx(
                        protocol_key,
                        "AgentBridgeActivationArgvJson",
                    )[0]
                )
            self.assertTrue(os.path.samefile(activation_argv[0], sys.executable))
            self.assertTrue(
                os.path.samefile(
                    activation_argv[1],
                    skill / "scripts" / "bridge.py",
                )
            )
            self.assertEqual(activation_argv[2:5], ["--as", "notification-action", "--data-root"])
            self.assertTrue(
                os.path.samefile(
                    activation_argv[5],
                    root / ".agent-bridge",
                )
            )
            shortcut = (
                Path(os.environ["APPDATA"])
                / "Microsoft"
                / "Windows"
                / "Start Menu"
                / "Programs"
                / "Agent Bridge.lnk"
            )
            self.assertTrue(shortcut.is_file())

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
                    "-DevSourceFallback",
                    "-Python",
                    sys.executable,
                    "-InstallRoot",
                    str(root),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=isolated_env,
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
            if tomllib is not None:
                reasonix = tomllib.loads(
                    (root / ".reasonix" / "config.toml").read_text(encoding="utf-8")
                )
                managed = next(item for item in reasonix["plugins"] if item["name"] == "agent-bridge")
                self.assertEqual(managed["command"], sys.executable)
                self.assertTrue(any("agent_bridge.adapters.integration" in argument for argument in managed["args"]))
                tomllib.loads(
                    (root / ".codex" / "config.toml").read_text(encoding="utf-8")
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
                    "-DevSourceFallback",
                    "-InstallRoot",
                    str(root),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=isolated_env,
                timeout=60,
            )
            self.assertEqual(
                uninstall.returncode,
                0,
                uninstall.stdout + uninstall.stderr,
            )
            self.assertFalse((root / ".local" / "bin" / "bridge.cmd").exists())
            self.assertFalse((root / ".agent-bridge" / "skill").exists())
            self.assertFalse((root / ".agent-bridge" / "native").exists())
            self.assertFalse(shortcut.exists())
            user_helper = subprocess.run(
                [
                    powershell,
                    "-NoLogo",
                    "-NoProfile",
                    "-Command",
                    "[Environment]::GetEnvironmentVariable("
                    "'AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER','User')",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=isolated_env,
                timeout=15,
            )
            # An isolated installer run must restore, rather than overwrite,
            # any pre-existing user helper setting from another installation.
            self.assertEqual(user_helper.stdout.strip(), prior_user_helper)
            dist_helper = (
                ROOT
                / "native"
                / "windows-notify"
                / "dist"
                / "windows-x86_64"
                / "agent-bridge-windows-notify.exe"
            )
            unregistered = subprocess.run(
                [str(dist_helper)],
                input='{"operation":"status"}',
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=isolated_env,
                timeout=15,
            )
            self.assertFalse(json.loads(unregistered.stdout)["ok"])
            zcode_bundle_root = (
                root
                / ".zcode"
                / "cli"
                / "plugins"
                / "cache"
                / "local"
                / "agent-bridge"
            )
            self.assertFalse(
                zcode_bundle_root.exists(),
                list(zcode_bundle_root.rglob("*")) if zcode_bundle_root.exists() else [],
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

    def test_notifier_ownership_checks_fail_closed_without_mutation(self):
        powershell = shutil.which("powershell.exe")
        self.assertIsNotNone(powershell)
        clean_env = self.isolated_subprocess_env()
        with tempfile.TemporaryDirectory() as first_tmp:
            first = Path(first_tmp)
            conflict = first / "unrelated-notifier.exe"
            conflict.write_bytes(b"unrelated")
            conflict_env = dict(clean_env)
            conflict_env["AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER"] = str(conflict)
            refused = subprocess.run(
                [
                    powershell,
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(WINDOWS_INSTALLER),
                    "-Agent",
                    "codex",
                    "-Python",
                    sys.executable,
                    "-InstallRoot",
                    str(first),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=conflict_env,
                timeout=30,
            )
            self.assertNotEqual(refused.returncode, 0)
            self.assertEqual(conflict.read_bytes(), b"unrelated")
            self.assertFalse((first / ".agent-bridge").exists())

        with tempfile.TemporaryDirectory() as second_tmp:
            root = Path(second_tmp)
            command = [
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(WINDOWS_INSTALLER),
                "-Agent",
                "codex",
                "-InstallRoot",
                str(root),
            ]
            install = subprocess.run(
                command + ["-Python", sys.executable],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=clean_env,
                timeout=60,
            )
            self.assertEqual(install.returncode, 0, install.stdout + install.stderr)
            notifier = root / ".agent-bridge" / "native"
            helper = notifier / "agent-bridge-windows-notify.exe"
            receipt_path = notifier / "receipt.json"
            original_helper = helper.read_bytes()
            original_receipt = receipt_path.read_bytes()
            try:
                helper.write_bytes(original_helper + b"tampered")
                tampered_helper = subprocess.run(
                    command + ["-Python", sys.executable],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=clean_env,
                    timeout=30,
                )
                self.assertNotEqual(tampered_helper.returncode, 0)
                self.assertEqual(helper.read_bytes(), original_helper + b"tampered")

                helper.write_bytes(original_helper)
                receipt = json.loads(original_receipt.decode("utf-8-sig"))
                receipt["owner"] = "unrelated"
                receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
                tampered_receipt = subprocess.run(
                    command + ["-Uninstall"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=clean_env,
                    timeout=30,
                )
                self.assertNotEqual(tampered_receipt.returncode, 0)
                self.assertTrue(helper.is_file())
                self.assertTrue((root / ".agent-bridge" / "skill").is_dir())
            finally:
                if helper.exists():
                    helper.write_bytes(original_helper)
                if receipt_path.parent.exists():
                    receipt_path.write_bytes(original_receipt)
                cleanup = subprocess.run(
                    command + ["-Uninstall"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=clean_env,
                    timeout=60,
                )
                self.assertEqual(cleanup.returncode, 0, cleanup.stdout + cleanup.stderr)


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


class DocumentationTests(unittest.TestCase):
    def test_docs_match_installers_delivery_and_lifecycle(self):
        english = (ROOT / "README.md").read_text(encoding="utf-8")
        chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        combined = english + chinese
        for token in (
            ".\\install.ps1 -Auto",
            "./install.sh --auto",
            "Python 3.9+",
            "queued",
            "dispatching",
            "os_posted",
            "plugin_delivered",
            "viewed",
            "launch_started",
            "agent_acknowledged",
            "claimed",
            "retry_wait",
            "failed",
            "input_required",
            "changes_requested",
            "python -m unittest discover -s tests -v",
            "macOS",
        ):
            self.assertIn(token, combined)
        self.assertIn("pending -> working", skill)
        self.assertIn("input_required -> pending", skill)
        self.assertIn("review_requested -> completed", skill)
        self.assertIn("status or inbox", skill)
        self.assertIn("not an acknowledgment", skill)
