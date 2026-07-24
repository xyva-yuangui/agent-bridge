"""v2 multi-process SQLite concurrency regression coverage."""

from __future__ import annotations

import concurrent.futures
import tempfile
import unittest
from pathlib import Path

from agent_bridge.service import BridgeService
from agent_bridge.store import Store


class ConcurrencyTests(unittest.TestCase):
    def test_concurrent_v2_sends_preserve_all_tasks_and_outbox_rows(self) -> None:
        """Service sends are intentionally synchronous: no detached worker owns the temp DB."""
        for repetition in range(5):
            with self.subTest(repetition=repetition), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                database = home / "agent-bridge.sqlite3"

                def send(index: int):
                    store = Store.open(database)
                    try:
                        return BridgeService(store).send_task(
                            "sender-{}".format(index), "zcode", "job-{}".format(index), "body"
                        )
                    finally:
                        store.close()

                with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
                    tasks = list(pool.map(send, range(30)))
                self.assertEqual({task.subject for task in tasks}, {"job-{}".format(index) for index in range(30)})
                verify = Store.open(database)
                try:
                    self.assertEqual(30, verify.scalar("SELECT COUNT(*) FROM tasks"))
                    self.assertEqual(30, verify.scalar("SELECT COUNT(*) FROM outbox WHERE completed_at IS NULL"))
                    self.assertEqual(0, verify.scalar("SELECT COUNT(*) FROM dispatcher_leases"))
                finally:
                    verify.close()


if __name__ == "__main__":
    unittest.main()
