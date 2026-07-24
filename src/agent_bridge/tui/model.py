"""Pure dashboard projection types and functions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence, Tuple


def sanitize_text(value: Any) -> str:
    """Make external data inert before it can reach a terminal renderer."""
    text = str(value)
    output: list[str] = []
    index = 0
    while index < len(text):
        character = text[index]
        code = ord(character)
        if character == "\x1b" and index + 1 < len(text):
            marker = text[index + 1]
            if marker == "[":
                index += 2
                while index < len(text) and not ("@" <= text[index] <= "~"):
                    index += 1
                index += 1
                continue
            if marker == "]":
                index += 2
                while index < len(text) and text[index] != "\x07" and not text.startswith("\x1b\\", index):
                    index += 1
                index += 2 if text.startswith("\x1b\\", index) else 1
                continue
        if code == 0x9B:
            index += 1
            while index < len(text) and not ("@" <= text[index] <= "~"):
                index += 1
            index += 1
            continue
        if code == 0x9D:
            index += 1
            while index < len(text) and text[index] not in ("\x07", "\x9c"):
                index += 1
            index += 1
            continue
        if code < 32 or 0x7F <= code <= 0x9F:
            output.append(" ")
        else:
            output.append(character)
        index += 1
    return "".join(output).strip()


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
    dependencies: Tuple[str, ...] = ()
    review_result: str = ""


@dataclass(frozen=True)
class Dashboard:
    agents: Tuple[Mapping[str, Any], ...]
    counts: DashboardCounts
    tasks: Tuple[DashboardTask, ...]
    selected: int = 0
    query: str = ""
    page_label: str = "page 1"
    sort_by: str = "updated"

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
    return Dashboard(agents, counts, tasks, safe_selected, query, sanitize_text(snapshot.get("page_label", "page 1")), sanitize_text(snapshot.get("sort_by", "updated")))


def _task(value: Any) -> DashboardTask:
    def field(name: str, default: Any = "") -> Any:
        return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)
    artifacts = field("artifacts", ()) or ()
    state = field("state")
    delivery = field("delivery", "")
    return DashboardTask(
        id=sanitize_text(field("id")), sender=sanitize_text(field("sender")), assignee=sanitize_text(field("assignee")),
        subject=sanitize_text(field("subject")), body=sanitize_text(field("body")), state=sanitize_text(getattr(state, "value", state)),
        delivery=sanitize_text(getattr(delivery, "value", delivery)), artifacts=tuple(sanitize_text(item) for item in artifacts),
        dependencies=tuple(sanitize_text(item) for item in (field("dependencies", ()) or ())), review_result=sanitize_text(field("review_result", "")),
    )


def _agent(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): sanitize_text(item) if not isinstance(item, (tuple, list)) else tuple(sanitize_text(part) for part in item) for key, item in value.items()}
    return {
        "name": sanitize_text(getattr(value, "name", "?")),
        "health": sanitize_text(getattr(value, "health", "unknown")),
        "execution_policy": sanitize_text(getattr(value, "execution_policy", "manual")),
        "capabilities": tuple(sanitize_text(item) for item in getattr(value, "capabilities", ())),
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
