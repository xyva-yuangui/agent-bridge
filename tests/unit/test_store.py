import sqlite3
import tempfile
import unittest
from pathlib import Path

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
        self.assertEqual(store.scalar("SELECT MAX(version) FROM schema_migrations"), 1)

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
