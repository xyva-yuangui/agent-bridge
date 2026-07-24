from __future__ import annotations

import multiprocessing
import tempfile
import unittest
from pathlib import Path

from agent_bridge.service import BridgeService
from agent_bridge.store import Store


CREATE_WORKERS = 40
CLAIM_WORKERS = 10


def _create_worker(database: str, index: int, gate, results) -> None:
    """Top-level worker: it must remain picklable under Windows spawn."""
    store = Store.open(Path(database))
    try:
        gate.wait()
        task = BridgeService(store).send_task("codex", "claude", "concurrent-{0}".format(index), "body")
        results.put(("ok", task.id))
    except BaseException as error:
        results.put(("error", "{0}: {1}".format(type(error).__name__, error)))
    finally:
        store.close()


def _claim_worker(database: str, task_id: str, gate, results) -> None:
    store = Store.open(Path(database))
    try:
        gate.wait()
        task = BridgeService(store).claim(task_id, "claude")
        results.put(("ok", task.id))
    except BaseException as error:
        results.put(("error", "{0}: {1}".format(type(error).__name__, error)))
    finally:
        store.close()


class SQLiteConcurrencyTests(unittest.TestCase):
    def test_spawn_processes_preserve_task_event_revision_and_database_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent-bridge.sqlite3"
            store = Store.open(path)
            service = BridgeService(store)
            claimable = [service.send_task("codex", "claude", "seed-{0}".format(index), "body").id for index in range(CLAIM_WORKERS)]
            store.close()

            create_results = self._run_workers(
                _create_worker, [(str(path), index) for index in range(CREATE_WORKERS)]
            )
            claim_results = self._run_workers(
                _claim_worker, [(str(path), task_id) for task_id in claimable]
            )
            self.assertTrue(all(status == "ok" for status, ignored in create_results), create_results)
            self.assertTrue(all(status == "ok" for status, ignored in claim_results), claim_results)
            created_ids = [value for status, value in create_results]
            self.assertEqual(len(created_ids), CREATE_WORKERS)
            self.assertEqual(len(set(created_ids)), CREATE_WORKERS)

            store = Store.open(path)
            try:
                self.assertEqual(store.scalar("SELECT COUNT(*) FROM tasks"), CREATE_WORKERS + CLAIM_WORKERS)
                self.assertEqual(store.scalar("SELECT COUNT(*) FROM outbox"), CREATE_WORKERS + CLAIM_WORKERS * 2)
                self.assertEqual(store.scalar("SELECT COUNT(*) FROM task_events"), CREATE_WORKERS + CLAIM_WORKERS * 2)
                self.assertEqual(store.scalar("SELECT COUNT(*) FROM tasks WHERE revision < 0"), 0)
                self.assertEqual(store.scalar("SELECT COUNT(*) FROM tasks WHERE id NOT IN (SELECT DISTINCT task_id FROM task_events)"), 0)
                for row in store.connection.execute(
                    "SELECT task_id, revision FROM task_events ORDER BY task_id, revision, id"
                ):
                    revisions = [entry[0] for entry in store.connection.execute(
                        "SELECT revision FROM task_events WHERE task_id = ? ORDER BY revision, id", (row["task_id"],)
                    )]
                    self.assertEqual(revisions, list(range(len(revisions))))
                self.assertEqual(store.integrity_report().message.lower(), "ok")
            finally:
                store.close()

    def _run_workers(self, worker, arguments):
        context = multiprocessing.get_context("spawn")
        gate = context.Barrier(len(arguments))
        results = context.Queue()
        processes = [context.Process(target=worker, args=(*argument, gate, results)) for argument in arguments]
        for process in processes:
            process.start()
        for process in processes:
            process.join(60)
        self.assertTrue(all(not process.is_alive() for process in processes), "worker did not exit")
        self.assertTrue(all(process.exitcode == 0 for process in processes), [process.exitcode for process in processes])
        output = [results.get(timeout=10) for ignored in processes]
        for process in processes:
            process.close()
        return output


if __name__ == "__main__":
    unittest.main()
