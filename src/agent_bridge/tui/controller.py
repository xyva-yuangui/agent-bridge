"""On-demand TUI controller: public service calls, input, and output only."""

from __future__ import annotations

import os
import shutil
import sys
from typing import Any, Mapping

from .input_common import Action
from .model import Dashboard, build_dashboard
from .render import render_compact, render_dashboard, truncate_cells


REFRESH_SECONDS = 0.25
PAGE_SIZE = 100


def run_tui(
    service: Any, input_adapter: Any, output: Any, *, actor: str = "unknown", project_id: str = "default",
    refresh_seconds: float = REFRESH_SECONDS,
) -> int:
    """Run while an interactive terminal exists; render one safe fallback otherwise."""
    interactive = (
        bool(getattr(output, "isatty", lambda: False)())
        and bool(getattr(input_adapter, "supported", True))
        and _supports_vt(output)
    )
    if not interactive:
        _write(output, render_compact(_dashboard(service, project_id), _terminal_size()[0]) + "\n")
        return 0
    selected = 0
    query = ""
    notice = ""
    try:
        with input_adapter:
            while True:
                dashboard = _dashboard(service, project_id, selected, query)
                selected = dashboard.selected
                width, height = _terminal_size()
                suffix = "\n" + truncate_cells(notice, width) if notice else ""
                _write(output, "\x1b[2J\x1b[H" + render_dashboard(dashboard, width, max(4, height - 1)) + suffix)
                action = input_adapter.read_key(max(0.25, min(0.5, refresh_seconds)))
                if action is None:
                    continue
                if action is Action.QUIT:
                    return 0
                if action is Action.UP:
                    selected = max(0, selected - 1)
                    continue
                if action is Action.DOWN:
                    selected = min(max(0, len(dashboard.tasks) - 1), selected + 1)
                    continue
                if action is Action.SEARCH:
                    reader = getattr(input_adapter, "read_line", None)
                    if callable(reader):
                        query = str(reader("filter: ") or "")
                        selected = 0
                        notice = "filter: " + query
                    else:
                        notice = "filter input unavailable"
                    continue
                task = dashboard.selected_task
                if task is None:
                    notice = "no task selected"
                    continue
                if action is Action.VIEW:
                    notice = _result("view", service.show(task.id))
                elif action is Action.CLAIM:
                    notice = _result("claim", service.claim(task.id, actor))
                elif action is Action.RETRY:
                    notice = _result("retry", service.retry_delivery(task.id))
                elif action is Action.OPEN:
                    notice = _result("terminal", service.open_terminal(task.id))
    except KeyboardInterrupt:
        return 130


def _dashboard(service: Any, project_id: str, selected: int = 0, query: str = "") -> Dashboard:
    page = service.board_page(project_id, limit=PAGE_SIZE, cursor=None)
    tasks = []
    for task in page.tasks:
        value = _as_mapping(task)
        evidence = service.delivery_evidence(str(value.get("id", "")))
        value["delivery"] = _evidence_text(evidence)
        tasks.append(value)
    return build_dashboard({"agents": tuple(service.agents()), "tasks": tasks}, selected, query)


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    fields = ("id", "sender", "assignee", "subject", "body", "state", "artifacts")
    return {field: getattr(value, field, "") for field in fields}


def _evidence_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return str(getattr(value, "status", value))


def _result(action: str, result: Any) -> str:
    return "{0}: {1}".format(action, result)


def _terminal_size() -> tuple[int, int]:
    size = shutil.get_terminal_size((80, 24))
    return size.columns, size.lines


def _supports_vt(output: Any) -> bool:
    """Honour explicit host capability and conventional non-VT terminal markers."""
    declared = getattr(output, "supports_vt", None)
    if declared is not None:
        return bool(declared)
    return os.environ.get("TERM", "").lower() != "dumb"


def _write(output: Any, text: str) -> None:
    output.write(text)
    flush = getattr(output, "flush", None)
    if callable(flush):
        flush()


def default_input_adapter() -> Any:
    """Delay platform imports so either adapter can be imported everywhere."""
    if sys.platform == "win32":
        from .input_windows import WindowsInputAdapter
        return WindowsInputAdapter()
    from .input_posix import PosixInputAdapter
    return PosixInputAdapter()
