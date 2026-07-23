from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from tests.support import read_board, run_bridge, write_agent


class EndToEndWorkflowTests(unittest.TestCase):
    def test_send_question_answer_review_changes_and_completion(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            write_agent(home, "alice", skills=["planning"])
            write_agent(home, "bob", skills=["review"])

            def run_as(name: str, *args: str):
                result = run_bridge(
                    home,
                    "--as",
                    name,
                    *args,
                    extra_env={"PYTHONUTF8": "1"},
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                return result

            sent = run_as(
                "alice",
                "send",
                "--to",
                "bob",
                "--subject",
                "end-to-end",
                "--no-wake",
            )
            task_id = re.search(r"\b[0-9a-f]{12}\b", sent.stdout).group(0)

            run_as("bob", "status", "--oneliner")
            run_as("bob", "claim", task_id)
            run_as("bob", "question", task_id, "--body", "Need a decision")
            run_as("alice", "status", "--oneliner")
            run_as("alice", "answer", task_id, "--body", "Use option A")
            run_as("bob", "status", "--oneliner")
            run_as("bob", "claim", task_id)
            run_as("bob", "review", task_id)
            run_as("alice", "status", "--oneliner")
            run_as(
                "alice",
                "review",
                task_id,
                "--verdict",
                "changes",
                "--body",
                "Add the edge case",
            )
            run_as("bob", "status", "--oneliner")
            run_as("bob", "claim", task_id)
            run_as("bob", "done", task_id, "--result", "Implemented and tested")

            task = next(
                task
                for task in read_board(home)["tasks"]
                if task["id"] == task_id
            )
            self.assertEqual(task["status"], "completed")
            self.assertEqual(task["result"], "Implemented and tested")
            self.assertEqual(task["answer"], "Use option A")
            self.assertEqual(task["review_verdict"], "changes")
            self.assertEqual(task["delivery"]["status"], "acknowledged")


if __name__ == "__main__":
    unittest.main()
