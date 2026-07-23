import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_bridge.models import TaskState
from agent_bridge.outbox import due_items
from agent_bridge.service import BridgeService
from agent_bridge.store import Store


class ServiceWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = Store.open(Path(self.temporary_directory.name) / "agent-bridge.sqlite3")
        self.service = BridgeService(self.store)

    def tearDown(self):
        self.store.close()
        self.temporary_directory.cleanup()

    def count_events(self, task_id):
        return self.store.scalar("SELECT COUNT(*) FROM task_events WHERE task_id = ?", (task_id,))

    def test_question_answer_review_round_trip(self):
        task = self.service.send_task("codex", "zcode", "Review", "Body")
        self.service.claim(task.id, "zcode")
        self.service.question(task.id, "zcode", "Which platform?")
        self.service.answer(task.id, "codex", "Both")
        self.service.claim(task.id, "zcode")
        self.service.request_review(task.id, "zcode", "Ready")
        final = self.service.review(task.id, "codex", "approve", "Approved")

        self.assertEqual(final.state, TaskState.COMPLETED)
        self.assertEqual(self.count_events(task.id), 7)
        self.assertEqual(final.revision, 6)

    def test_task_event_and_outbox_are_rolled_back_when_enqueue_fails(self):
        task = self.service.send_task("codex", "zcode", "Review", "Body")
        before_events = self.count_events(task.id)
        before_outbox = self.store.scalar("SELECT COUNT(*) FROM outbox")

        with patch("agent_bridge.service.enqueue", side_effect=RuntimeError("outbox failed")):
            with self.assertRaisesRegex(RuntimeError, "outbox failed"):
                self.service.claim(task.id, "zcode")

        self.assertEqual(self.service.show(task.id).state, TaskState.PENDING)
        self.assertEqual(self.count_events(task.id), before_events)
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM outbox"), before_outbox)

    def test_action_delivery_is_addressed_to_the_other_participant(self):
        task = self.service.send_task("codex", "zcode", "Review", "Body")
        self.service.claim(task.id, "zcode")

        row = self.store.connection.execute(
            "SELECT kind, payload_json FROM outbox ORDER BY id DESC LIMIT 1"
        ).fetchone()

        self.assertEqual(row["kind"], "task.claim")
        self.assertEqual(json.loads(row["payload_json"])["recipient"], "codex")

    def test_mutation_rejects_an_outdated_expected_revision(self):
        task = self.service.send_task("codex", "zcode", "Review", "Body")

        with self.assertRaisesRegex(ValueError, "revision conflict"):
            self.service.claim(task.id, "zcode", expected_revision=task.revision + 1)

        current = self.service.show(task.id)
        self.assertEqual(current.state, TaskState.PENDING)
        self.assertEqual(current.revision, task.revision)

    def test_due_outbox_items_are_ordered_and_decode_the_delivery_payload(self):
        first = self.service.send_task("codex", "zcode", "First", "Body")
        second = self.service.send_task("codex", "zcode", "Second", "Body")

        items = due_items(self.store.connection, now="9999-12-31T23:59:59Z")

        self.assertEqual([item.payload["task_id"] for item in items], [first.id, second.id])
        self.assertTrue(all(item.payload["recipient"] == "zcode" for item in items))

    def test_queries_expose_current_task_views(self):
        first = self.service.send_task("codex", "zcode", "First", "Body")
        second = self.service.send_task("codex", "other", "Second", "Body", project_id="other-project")

        self.assertEqual([task.id for task in self.service.inbox("zcode")], [first.id])
        self.assertEqual([task.id for task in self.service.board("other-project")], [second.id])
        self.assertEqual([task.id for task in self.service.status("zcode")], [first.id])

    def test_inbox_routes_questions_and_review_requests_to_the_sender(self):
        task = self.service.send_task("codex", "zcode", "Review", "Body")
        self.service.claim(task.id, "zcode")
        self.service.question(task.id, "zcode", "Which platform?")

        self.assertEqual([view.id for view in self.service.inbox("codex")], [task.id])

        self.service.answer(task.id, "codex", "Both")
        self.service.claim(task.id, "zcode")
        self.service.request_review(task.id, "zcode", "Ready")
        self.assertEqual([view.id for view in self.service.inbox("codex")], [task.id])
