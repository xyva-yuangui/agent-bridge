"""Agent identity canonicalization at the service boundary."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_bridge.service import BridgeService, _canonical_agent_name
from agent_bridge.store import Store


class CanonicalAgentNameTests(unittest.TestCase):
    def test_known_hosts_normalize_case_and_aliases(self) -> None:
        self.assertEqual("reasonix", _canonical_agent_name("Reasonix"))
        self.assertEqual("codex", _canonical_agent_name("openai-codex"))
        self.assertEqual("claude", _canonical_agent_name("Claude-Code"))
        self.assertEqual("zcode", _canonical_agent_name("  ZCode  "))

    def test_unknown_agents_lowercase_without_forking(self) -> None:
        self.assertEqual("qoder", _canonical_agent_name("Qoder"))
        self.assertEqual("qoder", _canonical_agent_name(" qoder "))


class ServiceIdentityNormalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store.open(Path(self.directory.name) / "bridge.sqlite3")
        self.addCleanup(self.store.close)
        self.service = BridgeService(self.store)

    def test_send_stores_canonical_sender_and_assignee(self) -> None:
        task = self.service.send_task("Codex", "Reasonix", "subject", "body")
        self.assertEqual("codex", task.sender)
        self.assertEqual("reasonix", task.assignee)
        names = {
            row["name"]
            for row in self.store.connection.execute("SELECT name FROM agents").fetchall()
        }
        self.assertIn("codex", names)
        self.assertIn("reasonix", names)
        self.assertNotIn("Codex", names)
        self.assertNotIn("Reasonix", names)

    def test_mixed_case_actor_can_claim_and_inbox_matches(self) -> None:
        task = self.service.send_task("codex", "reasonix", "subject", "body")
        inbox = self.service.inbox("Reasonix")
        self.assertEqual([task.id], [item.id for item in inbox.tasks])
        claimed = self.service.claim(task.id, "Reasonix")
        self.assertEqual("working", claimed.state.value)


if __name__ == "__main__":
    unittest.main()
