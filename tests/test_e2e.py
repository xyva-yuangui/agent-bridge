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

            task = invoke("alice", "send", "--to", "bob", "--subject", "end-to-end", "--no-wake")["task"]
            invoke("bob", "claim", task["id"], "--no-wake")
            invoke("bob", "question", task["id"], "--body", "Need a decision", "--no-wake")
            invoke("alice", "answer", task["id"], "--body", "Use option A", "--no-wake")
            invoke("bob", "claim", task["id"], "--no-wake")
            invoke("bob", "review", task["id"], "--no-wake")
            changed = invoke("alice", "review", task["id"], "--verdict", "changes", "--body", "Revise", "--no-wake")
            self.assertEqual(changed["task"]["state"], "changes_requested")
            invoke("bob", "claim", task["id"], "--no-wake")
            complete = invoke("bob", "done", task["id"], "--result", "Done", "--files", "src/a.py,src/a.py", "--no-wake")
            self.assertEqual(complete["task"]["state"], "completed")
            self.assertEqual(complete["task"]["artifacts"], ["src/a.py"])


if __name__ == "__main__":
    unittest.main()
