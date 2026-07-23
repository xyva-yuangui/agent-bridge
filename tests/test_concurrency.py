from __future__ import annotations

import concurrent.futures
import json
import tempfile
import unittest
from pathlib import Path

from tests.support import run_bridge, write_agent


class ConcurrencyTests(unittest.TestCase):
    def test_concurrent_sends_preserve_every_task_and_valid_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            write_agent(home, "target", skills=["review"])
            seed = run_bridge(
                home,
                "--as",
                "seed",
                "send",
                "--to",
                "target",
                "--subject",
                "seed",
                "--no-wake",
                extra_env={
                    "PYTHONUTF8": "1",
                    "AGENT_BRIDGE_DISABLE_NOTIFY": "1",
                },
            )
            self.assertEqual(seed.returncode, 0, seed.stderr)

            def send(index: int):
                return run_bridge(
                    home,
                    "--as",
                    f"sender-{index}",
                    "send",
                    "--to",
                    "target",
                    "--subject",
                    f"job-{index}",
                    "--no-wake",
                    extra_env={
                        "PYTHONUTF8": "1",
                        "AGENT_BRIDGE_DISABLE_NOTIFY": "1",
                    },
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=40) as pool:
                results = list(pool.map(send, range(40)))

            failures = [
                (result.returncode, result.stdout, result.stderr)
                for result in results
                if result.returncode != 0
            ]
            self.assertEqual(failures, [])
            board_path = home / "projects" / "default" / "board.json"
            board = json.loads(board_path.read_text(encoding="utf-8"))
            self.assertEqual(len(board["tasks"]), 41)
            self.assertEqual(
                {task["subject"] for task in board["tasks"]},
                {"seed", *(f"job-{index}" for index in range(40))},
            )


if __name__ == "__main__":
    unittest.main()
