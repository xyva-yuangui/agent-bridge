from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_bridge.adapters import ADAPTER_TYPES
from agent_bridge.setup import status
from tests.support import ROOT


HOSTS = tuple(adapter.name for adapter in ADAPTER_TYPES)
FIXTURES = ROOT / "tests" / "fixtures" / "hosts"


def _runtime_cli_argv(home: Path, *arguments: str) -> list[str]:
    """Run the runtime copied by setup without relying on source PYTHONPATH."""
    program = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "from agent_bridge.cli import main; raise SystemExit(main(sys.argv[2:]))"
    )
    return [
        sys.executable, "-c", program,
        str(home / ".agent-bridge" / "skill" / "runtime"), *arguments,
    ]


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

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"), "PowerShell runtime is required")
    def test_one_auto_invocation_registers_all_hosts_and_preserves_unrelated_config(self) -> None:
        """Release acceptance: all four normal host fixtures share one setup run."""
        powershell = shutil.which("powershell.exe")
        assert powershell is not None
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "用户 [all-hosts]"
            originals: dict[str, bytes] = {}
            for adapter_type in ADAPTER_TYPES:
                adapter = adapter_type(home)
                source = FIXTURES / adapter.name / ("unrelated.toml" if adapter.fixture_suffix == ".toml" else "unrelated.json")
                adapter.config_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, adapter.config_path)
                originals[adapter.name] = adapter.config_path.read_bytes()

            environment = os.environ.copy()
            user_base = Path(temporary) / "wheel-user-base"
            environment["PYTHONUSERBASE"] = str(user_base)
            environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
            environment.pop("AGENT_BRIDGE_HOME", None)
            environment.pop("PYTHONPATH", None)
            command = [
                powershell, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                str(ROOT / "install.ps1"), "-Auto", "-Python", sys.executable,
                "-InstallRoot", str(home),
            ]
            installed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", env=environment, timeout=90)
            self.assertEqual(0, installed.returncode, installed.stdout + installed.stderr)
            self.assertNotIn("DEGRADED development fallback", installed.stdout + installed.stderr)
            origin = subprocess.run(
                [sys.executable, "-c", "import agent_bridge; print(agent_bridge.__file__)"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", env=environment, timeout=30,
            )
            self.assertEqual(0, origin.returncode, origin.stdout + origin.stderr)
            self.assertIn(str(user_base.resolve()).lower(), str(Path(origin.stdout.strip()).resolve()).lower())
            self.assertNotIn(str((ROOT / "src").resolve()).lower(), str(Path(origin.stdout.strip()).resolve()).lower())

            report = status(home=home)
            hosts = {item["host"]: item for item in report["hosts"]}
            self.assertEqual(set(HOSTS), set(hosts))
            for adapter_type in ADAPTER_TYPES:
                adapter = adapter_type(home)
                with self.subTest(host=adapter.name):
                    self.assertTrue(adapter.detect().found)
                    self.assertTrue(hosts[adapter.name]["installed"])
                    self.assertEqual("session_card", hosts[adapter.name]["capabilities"]["surface"])
                    self.assertTrue((home / ".agent-bridge" / "agents" / adapter.name / "agent.json").is_file())
                    receipt = json.loads(adapter.installation_artifact_path.read_text(encoding="utf-8"))
                    # The exact argv written into the real host configuration
                    # must start without inheriting the checkout's PYTHONPATH.
                    outside_checkout = home / "outside-checkout"; outside_checkout.mkdir(exist_ok=True)
                    started = subprocess.run(
                        receipt["entrypoint"], input='{"jsonrpc":"2.0","id":1,"method":"initialize"}\n',
                        capture_output=True, text=True, encoding="utf-8", errors="replace", env=environment, cwd=outside_checkout, timeout=30,
                    )
                    self.assertEqual(0, started.returncode, started.stdout + started.stderr)
                    self.assertIn('"serverInfo"', started.stdout)

            repaired = subprocess.run(
                _runtime_cli_argv(home, "setup", "--repair", "--home", str(home)),
                capture_output=True, text=True, encoding="utf-8", errors="replace", env=environment, timeout=60,
            )
            self.assertEqual(0, repaired.returncode, repaired.stdout + repaired.stderr)
            self.assertEqual(
                1,
                (home / ".codex" / "config.toml").read_text(encoding="utf-8").count("# >>> agent-bridge:codex >>>"),
            )

            removed = subprocess.run(
                [*command[:7], "-Uninstall", "-Auto", "-Python", sys.executable, "-InstallRoot", str(home)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", env=environment, timeout=90,
            )
            self.assertEqual(0, removed.returncode, removed.stdout + removed.stderr)
            for adapter_type in ADAPTER_TYPES:
                adapter = adapter_type(home)
                with self.subTest(uninstall=adapter.name):
                    self.assertFalse(adapter.installation_artifact_path.exists())
                    if adapter.fixture_suffix == ".toml":
                        self.assertEqual(originals[adapter.name], adapter.config_path.read_bytes())
                    else:
                        self.assertEqual(
                            json.loads(originals[adapter.name].decode("utf-8")),
                            json.loads(adapter.config_path.read_text(encoding="utf-8")),
                        )


if __name__ == "__main__":
    unittest.main()
