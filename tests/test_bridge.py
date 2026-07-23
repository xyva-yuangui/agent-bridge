from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from tests.support import load_bridge, read_board, run_bridge


OLD_TIMESTAMP = "2000-01-01T00:00:00Z"
RECENT_TIMESTAMP = "2099-01-01T00:00:00Z"


class BridgeTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.home = Path(self.temp_dir.name)
        self.bridge = load_bridge()
        self.bridge.BASE_DIR = self.home
        self.bridge.AGENTS_DIR = self.home / "agents"
        self.bridge.PROJECTS_DIR = self.home / "projects"
        self.bridge.ensure_dirs()

    @property
    def board_path(self) -> Path:
        return self.home / "projects" / "default" / "board.json"

    @property
    def archive_path(self) -> Path:
        return self.home / "projects" / "default" / "archive.json"

    @property
    def activity_path(self) -> Path:
        return self.home / "projects" / "default" / "activity.jsonl"

    def write_board(self, tasks: list[dict]) -> None:
        self.board_path.parent.mkdir(parents=True, exist_ok=True)
        self.board_path.write_text(
            json.dumps({"version": 1, "tasks": tasks}, indent=2),
            encoding="utf-8",
        )

    def task(self, task_id: str) -> dict:
        board = json.loads(self.board_path.read_text(encoding="utf-8"))
        return next(task for task in board["tasks"] if task["id"] == task_id)


class StorageTests(BridgeTestCase):
    @unittest.skipUnless(os.name == "nt", "portable lock is used on Windows")
    def test_portable_lock_recovers_dead_stale_owner(self):
        resource = self.home / "resource.json"
        resource.write_text("{}", encoding="utf-8")
        lock_path = Path(str(resource) + ".lock")
        lock_path.write_text(
            json.dumps({"pid": 2_147_483_647, "created": time.time() - 3600}),
            encoding="utf-8",
        )
        old_time = time.time() - 3600
        os.utime(lock_path, (old_time, old_time))

        acquired = self.bridge._portable_lock(str(resource), timeout=0.1)

        self.assertEqual(Path(acquired), lock_path)
        self.bridge._portable_unlock(acquired)
        self.assertFalse(lock_path.exists())

    def test_stale_working_task_is_failed(self):
        task_id = "stale-task"
        self.write_board(
            [
                {
                    "id": task_id,
                    "subject": "stale",
                    "body": "",
                    "from": "alice",
                    "to": "bob",
                    "status": "working",
                    "created": OLD_TIMESTAMP,
                    "updated": OLD_TIMESTAMP,
                    "files": [],
                    "project": "default",
                }
            ]
        )

        self.bridge._auto_stale_working("default", "codex")

        task = self.task(task_id)
        self.assertEqual(task["status"], "failed")
        self.assertIn("auto-failed", task["result"])

    def test_append_activity_rotates_and_keeps_valid_json_lines(self):
        self.bridge.MAX_ACTIVITY_ENTRIES = 4

        for index in range(7):
            self.bridge.append_activity(
                "default",
                {"agent": "agent", "action": "test", "n": index},
            )

        lines = self.activity_path.read_text(encoding="utf-8").splitlines()
        self.assertLessEqual(len(lines), 4)
        for line in lines:
            json.loads(line)

    def test_auto_clean_archives_removed_tasks(self):
        tasks = []
        for index in range(10):
            task_id = f"task-{index}"
            tasks.append(
                {
                    "id": task_id,
                    "subject": task_id,
                    "body": "",
                    "from": "alice",
                    "to": "bob",
                    "status": "completed" if index == 0 else "pending",
                    "created": OLD_TIMESTAMP if index == 0 else RECENT_TIMESTAMP,
                    "updated": OLD_TIMESTAMP if index == 0 else RECENT_TIMESTAMP,
                    "files": [],
                    "project": "default",
                }
            )
        self.write_board(tasks)

        self.bridge._auto_clean("default", "codex")

        board_ids = {task["id"] for task in read_board(self.home)["tasks"]}
        archive = json.loads(self.archive_path.read_text(encoding="utf-8"))
        archive_ids = {task["id"] for task in archive}
        self.assertNotIn("task-0", board_ids)
        self.assertIn("task-0", archive_ids)

    def test_clean_requires_explicit_scope(self):
        self.write_board(
            [
                {
                    "id": "complete",
                    "subject": "complete",
                    "body": "",
                    "from": "alice",
                    "to": "bob",
                    "status": "completed",
                    "created": OLD_TIMESTAMP,
                    "updated": OLD_TIMESTAMP,
                    "files": [],
                    "project": "default",
                }
            ]
        )

        result = run_bridge(self.home, "--as", "codex", "clean")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--all or --days", result.stderr)
        self.assertEqual(len(read_board(self.home)["tasks"]), 1)


if __name__ == "__main__":
    unittest.main()
