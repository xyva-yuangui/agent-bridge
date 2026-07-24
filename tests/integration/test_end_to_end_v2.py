from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_bridge.models import TaskState
from agent_bridge.service import BridgeService
from agent_bridge.store import Store


class EndToEndV2Tests(unittest.TestCase):
    def test_four_agent_workflow_keeps_lifecycle_and_delivery_evidence_separate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Store.open(Path(directory) / "agent-bridge.sqlite3")
            try:
                service = BridgeService(store)
                main = service.send_task("codex", "claude", "review", "please review")
                self._ack(service, main.id, "claude", "main-ack")
                self.assertEqual(service.show(main.id).state, TaskState.PENDING)
                self.assertEqual(service.delivery_evidence(main.id).status, "agent_acknowledged")

                claimed = service.claim(main.id, "claude")
                self.assertEqual(claimed.state, TaskState.WORKING)
                self.assertEqual(claimed.revision, 1)
                questioned = service.question(main.id, "claude", "Which target?")
                self.assertEqual(questioned.state, TaskState.INPUT_REQUIRED)
                answered = service.answer(main.id, "codex", "Windows and macOS")
                self.assertEqual(answered.state, TaskState.PENDING)
                reclaimed = service.claim(main.id, "claude")
                self.assertEqual(reclaimed.state, TaskState.WORKING)
                service.request_review(main.id, "claude", "ready")
                changed = service.review(main.id, "codex", "changes", "add proof")
                self.assertEqual(changed.state, TaskState.CHANGES_REQUESTED)
                self.assertEqual(service.claim(main.id, "claude").state, TaskState.WORKING)
                service.request_review(main.id, "claude", "proof added")
                approved = service.review(main.id, "codex", "approve", "approved")
                self.assertEqual(approved.state, TaskState.COMPLETED)
                self.assertGreaterEqual(approved.revision, 9)

                reasonix = service.send_task("claude", "reasonix", "handoff", "verify")
                self._ack(service, reasonix.id, "reasonix", "reasonix-ack")
                self.assertEqual(service.delivery_evidence(reasonix.id).status, "agent_acknowledged")
                self.assertEqual(service.claim(reasonix.id, "reasonix").state, TaskState.WORKING)
                self.assertEqual(service.done(reasonix.id, "reasonix", "verified").state, TaskState.COMPLETED)

                zcode = service.send_task("reasonix", "zcode", "delivery", "release")
                self._ack(service, zcode.id, "zcode", "zcode-ack")
                self.assertEqual(service.delivery_evidence(zcode.id).status, "agent_acknowledged")
                self.assertEqual(service.claim(zcode.id, "zcode").state, TaskState.WORKING)
                self.assertEqual(service.done(zcode.id, "zcode", "released").state, TaskState.COMPLETED)
                self.assertEqual(store.scalar("SELECT COUNT(*) FROM agents WHERE name IN ('codex', 'claude', 'reasonix', 'zcode')"), 4)
                self.assertEqual(store.integrity_report().message.lower(), "ok")
            finally:
                store.close()

    @staticmethod
    def _ack(service: BridgeService, task_id: str, agent: str, token: str) -> None:
        service.register_host_delivery_proof(task_id, agent, "1.0.0", 2, token)
        service.acknowledge_integration(task_id, agent, "1.0.0", 2, token)


if __name__ == "__main__":
    unittest.main()
