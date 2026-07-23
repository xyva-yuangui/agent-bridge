from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from agent_bridge.adapters import ADAPTER_TYPES

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hosts"


class HostConfigurationRoundTripTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.home = Path(self.directory.name) / "用户 [test]"
        self.home.mkdir()

    def tearDown(self) -> None:
        self.directory.cleanup()

    def _adapter(self, host: str):
        return next(item(self.home) for item in ADAPTER_TYPES if item.name == host)

    def _provision(self, adapter) -> None:
        adapter.marker_path.parent.mkdir(parents=True, exist_ok=True)
        adapter.marker_path.write_text(json.dumps({"host": adapter.name, "mechanisms": [adapter.mechanism]}), encoding="utf-8")

    def _copy_fixture(self, host: str, fixture: str) -> Path:
        adapter = self._adapter(host)
        self._provision(adapter)
        source = FIXTURES / host / fixture
        adapter.config_path.parent.mkdir(parents=True, exist_ok=True)
        if source.exists():
            shutil.copyfile(source, adapter.config_path)
        return adapter.config_path

    def test_absent_config_installs_only_for_a_detected_host(self) -> None:
        for adapter_type in ADAPTER_TYPES:
            with self.subTest(host=adapter_type.name):
                adapter = adapter_type(self.home)
                self._provision(adapter)
                self.assertFalse(adapter.config_path.exists())
                self.assertTrue(adapter.install().ok)
                self.assertTrue(adapter.uninstall().ok)
                self.assertNotIn("agent-bridge", adapter.config_path.read_text(encoding="utf-8"))

    @unittest.skipIf(tomllib is None, "tomllib is unavailable")
    def test_toml_install_uses_named_tables_without_capturing_user_keys(self) -> None:
        for host in ("codex", "reasonix"):
            with self.subTest(host=host):
                adapter = self._adapter(host)
                path = self._copy_fixture(host, "unrelated.toml")
                self.assertTrue(adapter.install().ok)
                parsed = tomllib.loads(path.read_text(encoding="utf-8"))
                if host == "codex":
                    self.assertIn("agent_bridge", parsed["mcp_servers"])
                    self.assertEqual(parsed["mcp_servers"]["unrelated"], {"command": "C:\\Tools\\普通\\server.exe"})
                else:
                    self.assertIn("agent_bridge", parsed)
                    self.assertEqual(parsed["agent"], {"model": "普通模型"})

    def test_toml_uninstall_preserves_unrelated_bytes(self) -> None:
        for host in ("codex", "reasonix"):
            with self.subTest(host=host):
                adapter = self._adapter(host)
                path = self._copy_fixture(host, "unrelated.toml")
                original = path.read_text(encoding="utf-8")
                adapter.install()
                adapter.uninstall()
                self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_json_uninstall_merges_away_only_managed_data_after_concurrent_edit(self) -> None:
        for host in ("claude", "zcode"):
            with self.subTest(host=host):
                adapter = self._adapter(host)
                path = self._copy_fixture(host, "unrelated.json")
                before = json.loads(path.read_text(encoding="utf-8"))
                adapter.install()
                current = json.loads(path.read_text(encoding="utf-8"))
                current["concurrent_edit"] = {"keep": "是"}
                path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                adapter.uninstall()
                restored = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(restored["concurrent_edit"], {"keep": "是"})
                self.assertEqual(restored["permissions"] if host == "claude" else restored["ui"], before["permissions"] if host == "claude" else before["ui"])
                self.assertNotIn("agent_bridge", restored)
                if host == "zcode":
                    self.assertNotIn("agent-bridge@local", restored["plugins"]["enabledPlugins"])
