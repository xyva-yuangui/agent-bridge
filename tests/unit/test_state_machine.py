from dataclasses import dataclass
import unittest

from agent_bridge.models import TaskState
from agent_bridge.state_machine import authorize_transition


@dataclass(frozen=True)
class Task:
    state: TaskState
    sender: str = "codex"
    assignee: str = "zcode"


LEGAL = {
    ("pending", "claim"): "working",
    ("working", "question"): "input_required",
    ("input_required", "answer"): "pending",
    ("working", "request_review"): "review_requested",
    ("review_requested", "approve"): "completed",
    ("review_requested", "changes"): "changes_requested",
    ("changes_requested", "claim"): "working",
    ("working", "done"): "completed",
    ("working", "fail"): "failed",
}


class TransitionTests(unittest.TestCase):
    def test_every_declared_transition(self):
        for (source, action), target in LEGAL.items():
            with self.subTest(source=source, action=action):
                task = Task(TaskState(source))
                actor = task.assignee if action not in {"answer", "approve", "changes"} else task.sender
                self.assertEqual(authorize_transition(task, actor, action).value, target)

    def test_every_action_rejects_the_wrong_actor(self):
        for (source, action), _target in LEGAL.items():
            with self.subTest(source=source, action=action):
                task = Task(TaskState(source))
                actor = task.sender if action not in {"answer", "approve", "changes"} else task.assignee
                with self.assertRaises(PermissionError):
                    authorize_transition(task, actor, action)

    def test_every_action_rejects_wrong_source_state(self):
        for action in {action for _source, action in LEGAL}:
            legal_sources = {source for (source, candidate) in LEGAL if candidate == action}
            for source in TaskState:
                if source.value in legal_sources:
                    continue
                with self.subTest(action=action, source=source.value):
                    actor = "codex" if action in {"answer", "approve", "changes"} else "zcode"
                    with self.assertRaises(ValueError):
                        authorize_transition(Task(source), actor, action)

    def test_unknown_action_is_rejected(self):
        with self.assertRaises(ValueError):
            authorize_transition(Task(TaskState.PENDING), "zcode", "archive")
