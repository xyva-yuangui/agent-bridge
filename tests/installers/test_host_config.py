from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from agent_bridge.adapters import ADAPTER_TYPES


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hosts"


class HostConfigurationRoundTripTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.home = Path(self.directory.name) / "用户 [test]"
        self.home.mkdir()

    def tearDown(self) -> None:
        self.directory.cleanup()

    def _copy_fixture(self, host: str, fixture: str) -> Path:
        adapter = next(item(self.home) for item in ADAPTER_TYPES if item.name == host)
        source = FIXTURES / host / fixture
        adapter.config_path.parent.mkdir(parents=True, exist_ok=True)
        if source.exists():
            shutil.copyfile(source, adapter.config_path)
        return adapter.config_path

    def test_absent_config_is_installed_and_removed_without_leaving_managed_content(self) -> None:
        for adapter_type in ADAPTER_TYPES:
            with self.subTest(host=adapter_type.name):
                adapter = adapter_type(self.home)
                self.assertFalse(adapter.config_path.exists())
                self.assertTrue(adapter.install().ok)
                self.assertTrue(adapter.config_path.exists())
                self.assertTrue(adapter.uninstall().ok)
                self.assertNotIn("agent-bridge", adapter.config_path.read_text(encoding="utf-8"))

    def test_unrelated_config_survives_install_and_uninstall(self) -> None:
        for adapter_type in ADAPTER_TYPES:
            with self.subTest(host=adapter_type.name):
                adapter = adapter_type(self.home)
                path = self._copy_fixture(adapter.name, "unrelated" + adapter.fixture_suffix)
                original = path.read_text(encoding="utf-8")

                self.assertTrue(adapter.install().ok)
                self.assertTrue(adapter.uninstall().ok)

                self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_older_managed_config_is_repaired_then_removed(self) -> None:
        for adapter_type in ADAPTER_TYPES:
            with self.subTest(host=adapter_type.name):
                adapter = adapter_type(self.home)
                path = self._copy_fixture(adapter.name, "older-managed" + adapter.fixture_suffix)

                self.assertTrue(adapter.install().ok)
                self.assertIn(adapter.capabilities().integration_version, path.read_text(encoding="utf-8"))
                self.assertTrue(adapter.uninstall().ok)
                self.assertNotIn("agent-bridge", path.read_text(encoding="utf-8"))

    def test_uninstall_removes_older_managed_json_without_a_repair_step(self) -> None:
        adapter = next(item(self.home) for item in ADAPTER_TYPES if item.name == "zcode")
        path = self._copy_fixture("zcode", "older-managed.json")

        self.assertTrue(adapter.uninstall().ok)

        self.assertNotIn("agent-bridge", path.read_text(encoding="utf-8"))

    def test_json_hosts_preserve_unrelated_values(self) -> None:
        for adapter_type in (item for item in ADAPTER_TYPES if item.fixture_suffix == ".json"):
            with self.subTest(host=adapter_type.name):
                adapter = adapter_type(self.home)
                path = self._copy_fixture(adapter.name, "unrelated.json")
                before = json.loads(path.read_text(encoding="utf-8"))
                adapter.install()
                adapter.uninstall()
                self.assertEqual(json.loads(path.read_text(encoding="utf-8")), before)
