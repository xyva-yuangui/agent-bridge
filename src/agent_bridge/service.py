"""Application service for durable Agent Bridge task workflows."""

from __future__ import annotations

import base64
import json
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Iterator, List, Optional, Tuple

from .models import DeliveryStatus, TaskState
from .outbox import enqueue, utc_now
from .state_machine import authorize_transition
from .store import Store


@dataclass(frozen=True)
class TaskView:
    """The current, read-only projection of a task row."""

    id: str
    project_id: str
    sender: str
    assignee: str
    state: TaskState
    subject: str
    body: str
    priority: int
    revision: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class TaskPage:
    """A bounded task page with a cursor for the next stable slice."""

    tasks: Tuple[TaskView, ...]
    next_cursor: Optional[str]

    def __iter__(self) -> Iterator[TaskView]:
        return iter(self.tasks)

    def __len__(self) -> int:
        return len(self.tasks)

    def __getitem__(self, index: int) -> TaskView:
        return self.tasks[index]


MAX_INBOX_PAGE_SIZE = 100


class BridgeService:
    """Coordinates authorized state changes with their durable delivery work."""

    def __init__(self, store: Store) -> None:
        self.store = store

    def send_task(
        self,
        sender: str,
        assignee: str,
        subject: str,
        body: str,
        project_id: str = "default",
    ) -> TaskView:
        """Create a pending task and its initial delivery intent atomically."""
        task_id = uuid.uuid4().hex
        timestamp = utc_now()
        with self.store.transaction(immediate=True) as connection:
            self._ensure_participants(connection, project_id, sender, assignee)
            connection.execute(
                "INSERT INTO tasks("
                "id, project_id, sender, assignee, state, subject, body, priority, revision, "
                "created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task_id, project_id, sender, assignee, TaskState.PENDING.value, subject, body,
                    0, 0, timestamp, timestamp,
                ),
            )
            self._append_event(
                connection, task_id, 0, "task.created", sender,
                {"body": body, "subject": subject}, timestamp,
            )
            self._enqueue_delivery(
                connection, task_id, 0, "task.created", assignee, sender, timestamp
            )
        return self.show(task_id)

    def claim(
        self, task_id: str, actor: str, body: str = "", expected_revision: Optional[int] = None,
    ) -> TaskView:
        return self._mutate(task_id, actor, "claim", body, expected_revision)

    def question(
        self, task_id: str, actor: str, body: str, expected_revision: Optional[int] = None,
    ) -> TaskView:
        return self._mutate(task_id, actor, "question", body, expected_revision)

    def answer(
        self, task_id: str, actor: str, body: str, expected_revision: Optional[int] = None,
    ) -> TaskView:
        return self._mutate(task_id, actor, "answer", body, expected_revision)

    def request_review(
        self, task_id: str, actor: str, body: str = "", expected_revision: Optional[int] = None,
    ) -> TaskView:
        return self._mutate(task_id, actor, "request_review", body, expected_revision)

    def review(
        self, task_id: str, actor: str, verdict: str, body: str = "", expected_revision: Optional[int] = None,
    ) -> TaskView:
        if verdict not in ("approve", "changes"):
            raise ValueError("unknown review verdict: {0}".format(verdict))
        return self._mutate(task_id, actor, verdict, body, expected_revision)

    def done(
        self, task_id: str, actor: str, body: str = "", expected_revision: Optional[int] = None,
    ) -> TaskView:
        return self._mutate(task_id, actor, "done", body, expected_revision)

    def fail(
        self, task_id: str, actor: str, body: str = "", expected_revision: Optional[int] = None,
    ) -> TaskView:
        return self._mutate(task_id, actor, "fail", body, expected_revision)

    def status(self, agent: str) -> List[TaskView]:
        """Return the agent's current assigned tasks, newest first."""
        return self._query_tasks("assignee = ?", (agent,))

    def inbox(
        self, agent: str, limit: int = MAX_INBOX_PAGE_SIZE, cursor: Optional[str] = None
    ) -> TaskPage:
        """Return one stable, bounded page of work actionable by *agent*."""
        return self._query_task_page(
            "(assignee = ? AND state IN (?, ?)) "
            "OR (sender = ? AND state IN (?, ?))",
            (
                agent, TaskState.PENDING.value, TaskState.CHANGES_REQUESTED.value,
                agent, TaskState.INPUT_REQUIRED.value, TaskState.REVIEW_REQUESTED.value,
            ),
            limit,
            cursor,
        )

    def show(self, task_id: str) -> TaskView:
        row = self.store.connection.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise KeyError("unknown task: {0}".format(task_id))
        return _task_view(row)

    def board(self, project_id: str) -> List[TaskView]:
        return self._query_tasks("project_id = ?", (project_id,))

    def _mutate(
        self,
        task_id: str,
        actor: str,
        action: str,
        body: str,
        expected_revision: Optional[int],
    ) -> TaskView:
        with self.store.transaction(immediate=True) as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise KeyError("unknown task: {0}".format(task_id))
            task = _task_view(row)
            if expected_revision is not None and task.revision != expected_revision:
                raise ValueError(
                    "task revision conflict: expected {0}, found {1}".format(
                        expected_revision, task.revision
                    )
                )
            target_state = authorize_transition(task, actor, action)
            new_revision = task.revision + 1
            timestamp = utc_now()
            changed = connection.execute(
                "UPDATE tasks SET state = ?, revision = ?, updated_at = ? "
                "WHERE id = ? AND revision = ?",
                (target_state.value, new_revision, timestamp, task.id, task.revision),
            ).rowcount
            if changed != 1:
                raise RuntimeError("task revision changed before update")
            self._append_event(
                connection, task.id, new_revision, "task.{0}".format(action), actor,
                {"body": body, "from": task.state.value, "to": target_state.value}, timestamp,
            )
            recipient = task.sender if actor == task.assignee else task.assignee
            self._enqueue_delivery(
                connection, task.id, new_revision, "task.{0}".format(action), recipient, actor, timestamp
            )
            updated = connection.execute("SELECT * FROM tasks WHERE id = ?", (task.id,)).fetchone()
        return _task_view(updated)

    @staticmethod
    def _ensure_participants(
        connection: sqlite3.Connection, project_id: str, sender: str, assignee: str
    ) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO projects(id, path) VALUES (?, ?)", (project_id, project_id)
        )
        connection.executemany(
            "INSERT OR IGNORE INTO agents(name) VALUES (?)", ((sender,), (assignee,))
        )

    @staticmethod
    def _append_event(
        connection: sqlite3.Connection,
        task_id: str,
        revision: int,
        kind: str,
        actor: str,
        payload: dict,
        timestamp: str,
    ) -> None:
        connection.execute(
            "INSERT INTO task_events(task_id, revision, kind, actor, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (task_id, revision, kind, actor, json.dumps(payload, sort_keys=True), timestamp),
        )

    @staticmethod
    def _enqueue_delivery(
        connection: sqlite3.Connection,
        task_id: str,
        revision: int,
        kind: str,
        recipient: str,
        actor: str,
        timestamp: str,
    ) -> None:
        enqueue(
            connection,
            "{0}:{1}:{2}".format(task_id, revision, kind),
            kind,
            {
                "actor": actor,
                "delivery_status": DeliveryStatus.QUEUED.value,
                "recipient": recipient,
                "task_id": task_id,
            },
            timestamp,
        )

    def _query_tasks(self, where: str, parameters: tuple) -> List[TaskView]:
        rows = self.store.connection.execute(
            "SELECT * FROM tasks WHERE {0} ORDER BY updated_at DESC, id ASC".format(where), parameters
        )
        return [_task_view(row) for row in rows]

    def _query_task_page(
        self, where: str, parameters: tuple, limit: int, cursor: Optional[str]
    ) -> TaskPage:
        if limit < 1 or limit > MAX_INBOX_PAGE_SIZE:
            raise ValueError("limit must be between 1 and {0}".format(MAX_INBOX_PAGE_SIZE))
        cursor_parameters = ()
        if cursor is not None:
            updated_at, task_id = _decode_cursor(cursor)
            cursor_parameters = (updated_at, updated_at, task_id)
            where = "({0}) AND (updated_at < ? OR (updated_at = ? AND id > ?))".format(where)
        rows = self.store.connection.execute(
            "SELECT * FROM tasks WHERE {0} ORDER BY updated_at DESC, id ASC LIMIT ?".format(where),
            parameters + cursor_parameters + (limit + 1,),
        ).fetchall()
        tasks = tuple(_task_view(row) for row in rows[:limit])
        next_cursor = None
        if len(rows) > limit:
            last_task = tasks[-1]
            next_cursor = _encode_cursor(last_task.updated_at, last_task.id)
        return TaskPage(tasks=tasks, next_cursor=next_cursor)


def _task_view(row: sqlite3.Row) -> TaskView:
    return TaskView(
        id=row["id"],
        project_id=row["project_id"],
        sender=row["sender"],
        assignee=row["assignee"],
        state=TaskState(row["state"]),
        subject=row["subject"],
        body=row["body"],
        priority=row["priority"],
        revision=row["revision"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _encode_cursor(updated_at: str, task_id: str) -> str:
    payload = json.dumps(
        {"id": task_id, "updated_at": updated_at}, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> Tuple[str, str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode((cursor + padding).encode("ascii")))
        updated_at = payload["updated_at"]
        task_id = payload["id"]
    except (KeyError, TypeError, ValueError, UnicodeError) as error:
        raise ValueError("invalid task cursor") from error
    if not isinstance(updated_at, str) or not isinstance(task_id, str):
        raise ValueError("invalid task cursor")
    return updated_at, task_id
