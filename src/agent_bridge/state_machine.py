"""Task lifecycle transition rules and authorization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Protocol

from .models import TaskState


ASSIGNEE = "assignee"
SENDER = "sender"


class TaskLike(Protocol):
    """The task fields needed to decide a lifecycle transition."""

    state: TaskState
    sender: str
    assignee: str


@dataclass(frozen=True)
class Transition:
    sources: FrozenSet[TaskState]
    target: TaskState
    actor_role: str


RULES = {
    "claim": Transition(frozenset((TaskState.PENDING, TaskState.CHANGES_REQUESTED)), TaskState.WORKING, ASSIGNEE),
    "question": Transition(frozenset((TaskState.WORKING,)), TaskState.INPUT_REQUIRED, ASSIGNEE),
    "answer": Transition(frozenset((TaskState.INPUT_REQUIRED,)), TaskState.PENDING, SENDER),
    "request_review": Transition(frozenset((TaskState.WORKING,)), TaskState.REVIEW_REQUESTED, ASSIGNEE),
    "approve": Transition(frozenset((TaskState.REVIEW_REQUESTED,)), TaskState.COMPLETED, SENDER),
    "changes": Transition(frozenset((TaskState.REVIEW_REQUESTED,)), TaskState.CHANGES_REQUESTED, SENDER),
    "done": Transition(frozenset((TaskState.WORKING,)), TaskState.COMPLETED, ASSIGNEE),
    "fail": Transition(frozenset((TaskState.WORKING,)), TaskState.FAILED, ASSIGNEE),
}


def authorize_transition(task: TaskLike, actor: str, action: str) -> TaskState:
    """Authorize *actor* to perform *action* and return the target state."""
    transition = RULES.get(action)
    if transition is None:
        raise ValueError("unknown task action: {0}".format(action))
    if task.state not in transition.sources:
        raise ValueError(
            "action {0} is not allowed while task is {1}".format(action, task.state.value)
        )
    expected_actor = getattr(task, transition.actor_role)
    if actor != expected_actor:
        raise PermissionError(
            "only task {0} {1} may {2}".format(transition.actor_role, expected_actor, action)
        )
    return transition.target
