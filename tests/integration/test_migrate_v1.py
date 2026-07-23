import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

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

    def test_concurrent_importers_have_one_winner_and_one_zero_import(self):
        reports = []
        errors = []
        start = threading.Barrier(3)

        def import_board():
            store = None
            try:
                start.wait()
                store = Store.open(self.root / "agent-bridge.sqlite3")
                reports.append(import_v1(store, self.fixture_root))
            except BaseException as error:
                errors.append(error)
            finally:
                if store is not None:
                    store.close()

        threads = [
            threading.Thread(target=import_board),
            threading.Thread(target=import_board),
        ]
        for thread in threads:
            thread.start()
        start.wait()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(report.imported_tasks for report in reports), [0, 1])
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM import_ledger"), 1)

    def test_agent_profile_change_is_a_new_import_source(self):
        first = import_v1(self.store, self.fixture_root)
        source = self.root / "v1-source"
        shutil.copytree(self.fixture_root, source)
        profile_path = source / "agents" / "zcode" / "agent.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        profile["skills"].append("sqlite")
        profile_path.write_text(json.dumps(profile), encoding="utf-8")

        second = import_v1(self.store, source)

        self.assertEqual(first.imported_tasks, 1)
        self.assertEqual(second.imported_tasks, 0)
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM import_ledger"), 2)
        skills = json.loads(self.store.scalar(
            "SELECT capabilities_json FROM agents WHERE name = 'zcode'"
        ))
        self.assertIn("sqlite", skills)

    def test_export_uses_one_snapshot_while_a_writer_commits(self):
        import_v1(self.store, self.fixture_root)
        writer = Store.open(self.root / "agent-bridge.sqlite3")
        destination = self.root / "export" / "snapshot.json"
        from agent_bridge import migrate_v1

        original_rows = migrate_v1._rows
        wrote = []

        def rows_with_interleaved_write(store, table):
            rows = original_rows(store, table)
            if table == "tasks" and not wrote:
                wrote.append(True)
                with writer.transaction(immediate=True) as connection:
                    connection.execute("UPDATE tasks SET subject = 'new subject' WHERE id = 'task-1'")
                    connection.execute(
                        "INSERT INTO delivery_attempts("
                        "task_id, channel, status, attempts, created_at, updated_at"
                        ") VALUES (?, ?, ?, ?, ?, ?)",
                        ("task-1", "later", "viewed", 1, "2026-07-23T00:02:00Z", "2026-07-23T00:02:00Z"),
                    )
            return rows

        try:
            with mock.patch.object(migrate_v1, "_rows", side_effect=rows_with_interleaved_write):
                export_json(self.store, destination)
        finally:
            writer.close()

        exported = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual(exported["tasks"][0]["subject"], "Review")
        self.assertEqual(len(exported["delivery_attempts"]), 1)
