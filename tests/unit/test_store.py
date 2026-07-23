import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from agent_bridge.store import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.db_path = self.root / "agent-bridge.sqlite3"

    def tearDown(self):
        if hasattr(self, "store"):
            self.store.close()
        self.temporary_directory.cleanup()

    def test_open_applies_pragmas_and_initial_schema(self):
        self.store = Store.open(self.db_path)
        store = self.store

        self.assertEqual(store.scalar("PRAGMA journal_mode"), "wal")
        self.assertEqual(store.scalar("PRAGMA foreign_keys"), 1)
        self.assertEqual(store.scalar("PRAGMA busy_timeout"), 5000)
        self.assertEqual(store.scalar("SELECT MAX(version) FROM schema_migrations"), 3)

    def test_task_and_outbox_rollback_together(self):
        self.store = Store.open(self.db_path)
        store = self.store

        with self.assertRaises(RuntimeError):
            with store.transaction(immediate=True) as connection:
                connection.execute(
                    "INSERT INTO projects(id, path) VALUES (?, ?)",
                    ("default", str(self.root)),
                )
                connection.executemany(
                    "INSERT INTO agents(name) VALUES (?)", (("codex",), ("zcode",))
                )
                connection.execute(
                    "INSERT INTO tasks("
                    "id, project_id, sender, assignee, state, subject, body, "
                    "priority, revision, created_at, updated_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "task-1", "default", "codex", "zcode", "pending",
                        "Review", "", 0, 0,
                        "2026-07-23T00:00:00Z", "2026-07-23T00:00:00Z",
                    ),
                )
                connection.execute(
                    "INSERT INTO outbox("
                    "idempotency_key, kind, payload_json, due_at"
                    ") VALUES (?, ?, ?, ?)",
                    (
                        "task-1:created", "task.created",
                        '{"task_id":"task-1"}', "2026-07-23T00:00:00Z",
                    ),
                )
                raise RuntimeError("crash")

        self.assertEqual(store.scalar("SELECT COUNT(*) FROM tasks"), 0)
        self.assertEqual(store.scalar("SELECT COUNT(*) FROM outbox"), 0)

    def test_integrity_report_identifies_a_healthy_database(self):
        self.store = Store.open(self.db_path)
        store = self.store

        report = store.integrity_report()

        self.assertTrue(report.ok)
        self.assertEqual(report.message, "ok")

    def test_migration_checksum_mismatch_is_rejected(self):
        store = Store.open(self.db_path)
        store.close()
        connection = sqlite3.connect(str(self.db_path))
        connection.execute("UPDATE schema_migrations SET checksum = 'incorrect'")
        connection.commit()
        connection.close()

        with self.assertRaisesRegex(RuntimeError, "checksum"):
            Store.open(self.db_path)

    def test_concurrent_open_serializes_initial_migration(self):
        errors = []
        start = threading.Barrier(3)
        absence_check = threading.Barrier(2)
        original_exists = Store._migration_table_exists

        def synchronized_initial_absence(store):
            exists = original_exists(store)
            if not exists and not store.connection.in_transaction:
                absence_check.wait()
            return exists

        def open_store():
            store = None
            try:
                start.wait()
                store = Store.open(self.db_path)
            except BaseException as error:
                errors.append(error)
            finally:
                if store is not None:
                    store.close()

        with mock.patch.object(Store, "_migration_table_exists", synchronized_initial_absence):
            threads = [threading.Thread(target=open_store) for _ in range(2)]
            for thread in threads:
                thread.start()
            start.wait()
            for thread in threads:
                thread.join()

        self.assertEqual(errors, [])
        connection = sqlite3.connect(str(self.db_path))
        try:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version = 1"
            ).fetchone()[0], 1)
        finally:
            connection.close()

    def test_non_initial_migration_creates_readable_restorable_backup(self):
        store = Store.open(self.db_path)
        v2_migrations = list(store._migration_sources())
        store.close()
        third_migration = (4, "CREATE TABLE migration_backup_probe (id INTEGER PRIMARY KEY);")

        with mock.patch.object(
            Store, "_migration_sources", return_value=v2_migrations + [third_migration]
        ):
            upgraded = Store.open(self.db_path)
            upgraded.close()

        backups = list(self.root.glob("agent-bridge.sqlite3.before-v4.*.bak"))
        self.assertEqual(len(backups), 1)
        restored_path = self.root / "restored.sqlite3"
        restored_path.write_bytes(backups[0].read_bytes())
        restored = sqlite3.connect(str(restored_path))
        try:
            self.assertEqual(restored.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0], 3)
            self.assertIsNone(restored.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'migration_backup_probe'"
            ).fetchone())
            self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            restored.close()

    def test_v1_database_upgrades_to_v2_launch_reservations(self):
        source_store = Store.open(self.db_path)
        migrations = list(source_store._migration_sources())
        source_store.close()
        v1_migrations = [migration for migration in migrations if migration[0] == 1]
        legacy_path = self.root / "legacy-v1.sqlite3"

        with mock.patch.object(Store, "_migration_sources", return_value=v1_migrations):
            legacy = Store.open(legacy_path)
            legacy.close()

        upgraded = Store.open(legacy_path)
        try:
            self.assertEqual(upgraded.scalar("SELECT MAX(version) FROM schema_migrations"), 3)
            self.assertIsNotNone(upgraded.scalar(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'launch_reservations'"
            ))
        finally:
            upgraded.close()
