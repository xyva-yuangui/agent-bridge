"""v2 multi-process SQLite concurrency regression coverage."""

from __future__ import annotations

import concurrent.futures
import os
import tempfile
import unittest
from pathlib import Path

from tests.integration.test_cli_v2 import run_module


class ConcurrencyTests(unittest.TestCase):
    def test_concurrent_v2_sends_preserve_all_tasks_and_outbox_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)

            def send(index: int):
                return run_module(
                    "agent_bridge.cli", "--as", "sender-{}".format(index), "--json", "send",
                    "--to", "zcode", "--subject", "job-{}".format(index), home=home,
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
                results = list(pool.map(send, range(30)))
            self.assertTrue(all(result.returncode == 0 for result in results), [result.stderr for result in results if result.returncode])
            board = run_module("agent_bridge.cli", "--json", "board", home=home)
            self.assertEqual(board.returncode, 0, board.stderr)
            import json
            tasks = json.loads(board.stdout)["tasks"]
            self.assertEqual({task["subject"] for task in tasks}, {"job-{}".format(index) for index in range(30)})
            self.assertEqual(len(tasks), 30)


if __name__ == "__main__":
    unittest.main()
