from __future__ import annotations

import json
import os
import plistlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_bridge.path_ownership import PosixPathBackend
from agent_bridge.setup import apply_setup_plan, build_setup_plan, repair, status, uninstall


class _Notifier:
    calls = []
    def __init__(self, helper, activation_argv=()): self.helper, self.activation_argv = helper, tuple(activation_argv)
    def register(self, argv): self.calls.append(("register", tuple(argv))); return type("Result", (), {"ok": True, "detail": "ready"})()
    def unregister(self): self.calls.append(("unregister", self.activation_argv)); return type("Result", (), {"ok": True, "detail": "removed"})()


class MacOSSetupLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(); self.home = Path(self.temp.name) / "home"; self.home.mkdir()
        marker = self.home / ".codex" / "agent-bridge-host.json"; marker.parent.mkdir(); marker.write_text('{"host":"codex","mechanisms":["mcp"]}', encoding="utf-8")
        self.app = Path(self.temp.name) / "portable" / "AgentBridgeNotifier.app"; executable = self.app / "Contents" / "MacOS" / "AgentBridgeNotifier"; executable.parent.mkdir(parents=True)
        executable.write_bytes(b"unsigned-test-helper")
        (self.app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "org.agentbridge.notifier", "CFBundleExecutable": "AgentBridgeNotifier"}))
        self.backend = PosixPathBackend({}); _Notifier.calls = []

    def tearDown(self) -> None: self.temp.cleanup()

    def _apply(self):
        with patch("agent_bridge.setup.sys.platform", "darwin"), patch("agent_bridge.setup.MacOSNotifier", _Notifier), patch.dict(os.environ, {"AGENT_BRIDGE_MACOS_NOTIFY_APP": str(self.app)}, clear=False):
            return apply_setup_plan(build_setup_plan(home=self.home, auto=True), path_backend=self.backend)

    def test_receipted_install_repair_status_and_uninstall_are_owned(self) -> None:
        self._apply()
        app = self.home / ".agent-bridge" / "native" / "macos-universal2" / "AgentBridgeNotifier.app"
        receipt = app.parent / "receipt.json"
        owned = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual("agent-bridge.macos-notify", owned["owner"])
        self.assertEqual(os.sys.executable, owned["activation_argv"][0])
        self.assertEqual("--data-root", owned["activation_argv"][2])
        self.assertTrue(Path(owned["activation_argv"][1]).is_absolute())
        self.assertTrue(Path(owned["activation_argv"][3]).is_absolute())
        with patch("agent_bridge.setup.sys.platform", "darwin"):
            self.assertTrue(status(home=self.home, path_backend=self.backend)["notifications"]["available"])
        with patch("agent_bridge.setup.sys.platform", "darwin"), patch("agent_bridge.setup.MacOSNotifier", _Notifier), patch.dict(os.environ, {"AGENT_BRIDGE_MACOS_NOTIFY_APP": str(self.app)}, clear=False):
            repair(home=self.home, agent="codex", path_backend=self.backend)
            uninstall(home=self.home, agent="codex", path_backend=self.backend)
        self.assertFalse(app.exists()); self.assertFalse(receipt.exists())
        self.assertIn("register", [call[0] for call in _Notifier.calls]); self.assertIn("unregister", [call[0] for call in _Notifier.calls])

    def test_failed_registration_rolls_back_and_external_edit_refuses_removal(self) -> None:
        class Failing(_Notifier):
            def register(self, argv): return type("Result", (), {"ok": False, "detail": "no authorization"})()
        with patch("agent_bridge.setup.sys.platform", "darwin"), patch("agent_bridge.setup.MacOSNotifier", Failing), patch.dict(os.environ, {"AGENT_BRIDGE_MACOS_NOTIFY_APP": str(self.app)}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "registration failed"):
                apply_setup_plan(build_setup_plan(home=self.home, auto=True), path_backend=self.backend)
        self.assertFalse((self.home / ".agent-bridge" / "native" / "macos-universal2" / "AgentBridgeNotifier.app").exists())
        self._apply()
        executable = self.home / ".agent-bridge" / "native" / "macos-universal2" / "AgentBridgeNotifier.app" / "Contents" / "MacOS" / "AgentBridgeNotifier"
        executable.write_bytes(b"external edit")
        with patch("agent_bridge.setup.sys.platform", "darwin"), patch("agent_bridge.setup.MacOSNotifier", _Notifier):
            with self.assertRaisesRegex(RuntimeError, "unowned macOS"):
                uninstall(home=self.home, agent="codex", path_backend=self.backend)
        self.assertTrue(executable.exists())
