import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_bridge.models import TaskState
from agent_bridge.outbox import enqueue, due_items
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

    def test_task_creation_event_and_outbox_roll_back_when_enqueue_fails(self):
        with patch("agent_bridge.service.enqueue", side_effect=RuntimeError("outbox failed")):
            with self.assertRaisesRegex(RuntimeError, "outbox failed"):
                self.service.send_task("codex", "zcode", "Review", "Body")

        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM tasks"), 0)
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM task_events"), 0)
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM outbox"), 0)

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

    def test_mutation_accepts_the_current_expected_revision(self):
        task = self.service.send_task("codex", "zcode", "Review", "Body")

        claimed = self.service.claim(task.id, "zcode", expected_revision=task.revision)

        self.assertEqual(claimed.state, TaskState.WORKING)
        self.assertEqual(claimed.revision, task.revision + 1)

    def test_duplicate_outbox_enqueue_returns_the_existing_intent(self):
        with self.store.transaction(immediate=True) as connection:
            first = enqueue(
                connection, "task-1:0:task.created", "task.created",
                {"task_id": "task-1"}, "2026-07-23T00:00:00Z",
            )
            replay = enqueue(
                connection, "task-1:0:task.created", "task.created",
                {"task_id": "task-1", "ignored": True}, "2026-07-24T00:00:00Z",
            )

        self.assertEqual(replay, first)
        self.assertGreater(first.id, 0)
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM outbox"), 1)

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

    def test_inbox_uses_stable_bounded_pages_without_duplicates(self):
        task_ids = [
            self.service.send_task("codex", "zcode", "Task {0}".format(index), "Body").id
            for index in range(5)
        ]

        first = self.service.inbox("zcode", limit=2)
        second = self.service.inbox("zcode", limit=2, cursor=first.next_cursor)
        third = self.service.inbox("zcode", limit=2, cursor=second.next_cursor)

        returned_ids = [task.id for page in (first, second, third) for task in page.tasks]
        self.assertEqual(set(returned_ids), set(task_ids))
        self.assertEqual(len(returned_ids), len(set(returned_ids)))
        self.assertIsNone(third.next_cursor)

    def test_inbox_rejects_an_invalid_page_limit(self):
        with self.assertRaisesRegex(ValueError, "limit"):
            self.service.inbox("zcode", limit=0)

    def test_inbox_default_page_is_bounded(self):
        for index in range(101):
            self.service.send_task("codex", "zcode", "Task {0}".format(index), "Body")

        page = self.service.inbox("zcode")

        self.assertEqual(len(page), 100)
        self.assertIsNotNone(page.next_cursor)

    def test_done_persists_normalized_artifacts_with_the_transition(self):
        task = self.service.send_task("codex", "zcode", "Files", "Body")
        self.service.claim(task.id, "zcode")

        self.service.done(task.id, "zcode", "complete", artifacts=("src/a.py", " docs/b.md ", "src/a.py"))

        rows = self.store.connection.execute(
            "SELECT path FROM task_artifacts WHERE task_id = ? ORDER BY path", (task.id,)
        ).fetchall()
        self.assertEqual([row["path"] for row in rows], ["docs/b.md", "src/a.py"])

    def test_done_artifacts_roll_back_when_delivery_enqueue_fails(self):
        task = self.service.send_task("codex", "zcode", "Files", "Body")
        self.service.claim(task.id, "zcode")

        with patch("agent_bridge.service.enqueue", side_effect=RuntimeError("outbox failed")):
            with self.assertRaisesRegex(RuntimeError, "outbox failed"):
                self.service.done(task.id, "zcode", artifacts=("src/a.py",))

        self.assertEqual(self.service.show(task.id).state, TaskState.WORKING)
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM task_artifacts WHERE task_id = ?", (task.id,)), 0)

    def test_all_task_collections_include_persisted_artifacts(self):
        task = self.service.send_task("codex", "zcode", "Files", "Body")
        with self.store.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO task_artifacts(task_id, kind, path, metadata_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (task.id, "file", "src/a.py", "{}", task.created_at),
            )

        self.assertEqual(self.service.status("zcode")[0].artifacts, ("src/a.py",))
        self.assertEqual(self.service.inbox("zcode")[0].artifacts, ("src/a.py",))
        self.assertEqual(self.service.board("default")[0].artifacts, ("src/a.py",))

    def test_host_acknowledgement_claim_is_durable_and_rejects_a_replay(self):
        task = self.service.send_task("codex", "zcode", "Review", "Body")
        self.service.register_host_delivery_proof(task.id, "zcode", "1.0.0", 2, "one-time-token")

        self.service.acknowledge_integration(task.id, "zcode", "1.0.0", 2, "one-time-token")

        self.assertEqual(
            self.store.scalar(
                "SELECT COUNT(*) FROM delivery_attempts WHERE task_id = ? AND channel = ? AND status = 'agent_acknowledged'",
                (task.id, "host:zcode"),
            ),
            1,
        )
        self.assertTrue(self.service.host_acknowledgement_is_claimed(task.id, "zcode", "one-time-token"))
        with self.assertRaisesRegex(ValueError, "already consumed"):
            self.service.acknowledge_integration(task.id, "zcode", "1.0.0", 2, "one-time-token")

    def test_direct_forged_acknowledgement_cannot_create_delivery_evidence(self):
        task = self.service.send_task("codex", "zcode", "Review", "Body")

        with self.assertRaisesRegex(ValueError, "proof is missing"):
            self.service.acknowledge_integration(task.id, "zcode", "9.9.9", 2, "forged-token")

        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM delivery_attempts WHERE task_id = ?", (task.id,)), 0)
