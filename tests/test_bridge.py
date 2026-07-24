"""Regression coverage migrated from the retired v1 JSON-board bridge.

These tests intentionally use the public v2 service and CLI surface.  The
old lock-file, activity-log rotation, and JSON-board tests are replaced by the
SQLite transaction/outbox and authorization guarantees that supersede them.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_bridge.models import DeliveryStatus, TaskState
from agent_bridge.service import BridgeService
from agent_bridge.store import Store
from tests.integration.test_cli_v2 import run_module


class BridgeV2RegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name)
        self.store = Store.open(self.home / "agent-bridge.sqlite3")
        self.service = BridgeService(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_lifecycle_authorization_replaces_v1_board_mutation(self) -> None:
        task = self.service.send_task("alice", "bob", "workflow", "")
        with self.assertRaises(PermissionError):
            self.service.claim(task.id, "mallory")
        working = self.service.claim(task.id, "bob")
        waiting = self.service.question(working.id, "bob", "Need input")
        self.assertEqual(waiting.state, TaskState.INPUT_REQUIRED)
        pending = self.service.answer(task.id, "alice", "Use option A")
        reviewed = self.service.request_review(self.service.claim(pending.id, "bob").id, "bob")
        complete = self.service.review(reviewed.id, "alice", "approve", "accepted")
        self.assertEqual(complete.state, TaskState.COMPLETED)

    def test_delivery_evidence_never_claims_acknowledgement_without_proof(self) -> None:
        task = self.service.send_task("alice", "zcode", "review", "")
        self.assertEqual(self.service.delivery_evidence(task.id).status, DeliveryStatus.QUEUED.value)
        self.service.register_host_delivery_proof(task.id, "zcode", "1.0.0", 1, "opaque-token")
        self.service.acknowledge_integration(task.id, "zcode", "1.0.0", 1, "opaque-token")
        self.assertEqual(self.service.delivery_evidence(task.id).status, DeliveryStatus.AGENT_ACKNOWLEDGED.value)
        with self.assertRaises(ValueError):
            self.service.acknowledge_integration(task.id, "zcode", "1.0.0", 1, "opaque-token")

    def test_clean_requires_explicit_scope_and_preserves_active_tasks(self) -> None:
        task = self.service.send_task("alice", "bob", "active", "")
        missing_scope = run_module("agent_bridge.cli", "clean", home=self.home)
        self.assertNotEqual(missing_scope.returncode, 0)
        self.assertEqual(self.service.show(task.id).state, TaskState.PENDING)

    def test_revision_conflict_prevents_lost_updates(self) -> None:
        task = self.service.send_task("alice", "bob", "conflict", "")
        self.service.claim(task.id, "bob", expected_revision=task.revision)
        with self.assertRaisesRegex(ValueError, "revision conflict"):
            self.service.question(task.id, "bob", "stale", expected_revision=task.revision)


if __name__ == "__main__":
    unittest.main()
