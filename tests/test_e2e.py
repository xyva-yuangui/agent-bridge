"""End-to-end v2 CLI lifecycle regression."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.integration.test_cli_v2 import run_module


class EndToEndWorkflowTests(unittest.TestCase):
    def test_send_question_answer_changes_review_and_completion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)

            def invoke(actor: str, *arguments: str):
                result = run_module("agent_bridge.cli", "--as", actor, "--json", *arguments, home=home)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)

            task = invoke("alice", "send", "--to", "bob", "--subject", "end-to-end")["task"]
            invoke("bob", "claim", task["id"])
            invoke("bob", "question", task["id"], "--body", "Need a decision")
            invoke("alice", "answer", task["id"], "--body", "Use option A")
            invoke("bob", "claim", task["id"])
            invoke("bob", "review", task["id"])
            changed = invoke("alice", "review", task["id"], "--verdict", "changes", "--body", "Revise")
            self.assertEqual(changed["task"]["state"], "changes_requested")
            invoke("bob", "claim", task["id"])
            complete = invoke("bob", "done", task["id"], "--result", "Done", "--files", "src/a.py,src/a.py")
            self.assertEqual(complete["task"]["state"], "completed")
            self.assertEqual(complete["task"]["artifacts"], ["src/a.py"])


if __name__ == "__main__":
    unittest.main()
