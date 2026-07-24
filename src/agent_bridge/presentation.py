"""Stable, encoding-safe views shared by the CLI and MCP boundaries."""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, Iterable, Optional

from .service import TaskPage, TaskView


def configure_streams() -> None:
    """Keep command output usable when Windows selects a legacy code page."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")


def task_view(task: TaskView) -> Dict[str, Any]:
    return {
        "id": task.id,
        "project_id": task.project_id,
        "sender": task.sender,
        "assignee": task.assignee,
        "state": task.state.value,
        "subject": task.subject,
        "body": task.body,
        "priority": task.priority,
        "revision": task.revision,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "artifacts": list(task.artifacts),
    }


def task_page(page: TaskPage) -> Dict[str, Any]:
    return {"tasks": [task_view(task) for task in page.tasks], "next_cursor": page.next_cursor}


def tasks_view(tasks: Iterable[TaskView]) -> Dict[str, Any]:
    return {"tasks": [task_view(task) for task in tasks]}


def render(value: Dict[str, Any], as_json: bool) -> str:
    """Render a machine view or a deliberately plain-text human summary."""
    if as_json:
        return json.dumps(value, ensure_ascii=True, sort_keys=True)
    if "task" in value:
        task = value["task"]
        return "OK {0} {1} {2}".format(task["id"], task["state"], task["subject"])
    if "tasks" in value:
        tasks = value["tasks"]
        if not tasks:
            return "EMPTY no tasks"
        return "\n".join(
            "TASK {0} {1} {2} -> {3}".format(
                task["id"], task["state"], task["sender"], task["assignee"]
            ) for task in tasks
        )
    if "identity" in value:
        return "OK identity {0}".format(value["identity"])
    if "ok" in value:
        return "OK" if value["ok"] else "ERROR"
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def error_view(error: BaseException) -> Dict[str, str]:
    return {"error": str(error) or error.__class__.__name__}
