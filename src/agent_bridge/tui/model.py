"""Pure dashboard projection types and functions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence, Tuple


@dataclass(frozen=True)
class DashboardCounts:
    inbox: int = 0
    working: int = 0
    review: int = 0
    completed: int = 0
    failed: int = 0


@dataclass(frozen=True)
class DashboardTask:
    id: str
    sender: str
    assignee: str
    subject: str
    body: str
    state: str
    delivery: str = ""
    artifacts: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Dashboard:
    agents: Tuple[Mapping[str, Any], ...]
    counts: DashboardCounts
    tasks: Tuple[DashboardTask, ...]
    selected: int = 0
    query: str = ""

    @property
    def selected_task(self) -> DashboardTask | None:
        return self.tasks[self.selected] if self.tasks else None


def build_dashboard(snapshot: Mapping[str, Any], selected: int = 0, query: str = "") -> Dashboard:
    """Convert only supplied public-service data into a stable UI projection."""
    agents = tuple(_agent(value) for value in snapshot.get("agents", ()))
    raw_tasks: Sequence[Any] = snapshot.get("tasks", ())
    tasks = tuple(_task(value) for value in raw_tasks)
    if query:
        needle = query.casefold()
        tasks = tuple(task for task in tasks if needle in _searchable(task).casefold())
    counts = _counts(tasks)
    safe_selected = min(max(0, selected), max(0, len(tasks) - 1))
    return Dashboard(agents, counts, tasks, safe_selected, query)


def _task(value: Any) -> DashboardTask:
    def field(name: str, default: Any = "") -> Any:
        return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)
    artifacts = field("artifacts", ()) or ()
    state = field("state")
    delivery = field("delivery", "")
    return DashboardTask(
        id=str(field("id")), sender=str(field("sender")), assignee=str(field("assignee")),
        subject=str(field("subject")), body=str(field("body")), state=str(getattr(state, "value", state)),
        delivery=str(getattr(delivery, "value", delivery)), artifacts=tuple(str(item) for item in artifacts),
    )


def _agent(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {
        "name": str(getattr(value, "name", "?")),
        "health": str(getattr(value, "health", "unknown")),
        "execution_policy": str(getattr(value, "execution_policy", "manual")),
        "capabilities": tuple(getattr(value, "capabilities", ())),
    }


def _counts(tasks: Sequence[DashboardTask]) -> DashboardCounts:
    states = [task.state for task in tasks]
    return DashboardCounts(
        inbox=sum(state in ("pending", "input_required", "changes_requested") for state in states),
        working=states.count("working"), review=states.count("review_requested"),
        completed=states.count("completed"), failed=states.count("failed"),
    )


def _searchable(task: DashboardTask) -> str:
    return " ".join((task.id, task.sender, task.assignee, task.subject, task.body, task.state, task.delivery))
