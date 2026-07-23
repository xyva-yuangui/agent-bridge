import json
import shutil
import tempfile
import unittest
from pathlib import Path

from agent_bridge.migrate_v1 import export_json, import_v1
from agent_bridge.store import Store


class MigrateV1Tests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.fixture_root = Path(__file__).parents[1] / "fixtures" / "v1"
        self.store = Store.open(self.root / "agent-bridge.sqlite3")

    def tearDown(self):
        self.store.close()
        self.temporary_directory.cleanup()

    def test_import_is_idempotent_and_preserves_delivery_history(self):
        first = import_v1(self.store, self.fixture_root)
        second = import_v1(self.store, self.fixture_root)

        self.assertEqual(first.imported_tasks, 1)
        self.assertEqual(second.imported_tasks, 0)
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM tasks"), 1)
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM import_ledger"), 1)
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM delivery_attempts"), 1)
        self.assertTrue(first.backup_path.is_dir())

    def test_export_writes_portable_json_atomically(self):
        import_v1(self.store, self.fixture_root)
        destination = self.root / "export" / "bridge.json"

        written = export_json(self.store, destination)

        self.assertEqual(written, destination)
        exported = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual(exported["tasks"][0]["id"], "task-1")
        self.assertIn("zcode", [agent["name"] for agent in exported["agents"]])

    def test_import_backup_is_not_created_inside_its_source_tree(self):
        source = self.root / "v1-source"
        shutil.copytree(self.fixture_root, source)
        self.store.close()
        self.store = Store.open(source / "agent-bridge.sqlite3")

        report = import_v1(self.store, source)

        self.assertFalse(report.backup_path.is_relative_to(source))
