"""On-demand TUI controller: public service calls, input, and output only."""

from __future__ import annotations

import os
import shutil
import signal
import sys
import threading
from typing import Any, Mapping

from .input_common import Action
from .model import Dashboard, build_dashboard, sanitize_text
from .render import render_compact, render_dashboard, truncate_cells


REFRESH_SECONDS = 0.25
PAGE_SIZE = 100


def run_tui(
    service: Any, input_adapter: Any, output: Any, *, actor: str = "unknown", project_id: str = "default",
    refresh_seconds: float = REFRESH_SECONDS, dispatch_tick: Any = None,
) -> int:
    """Run while an interactive terminal exists; render one safe fallback otherwise."""
    interactive = (
        bool(getattr(output, "isatty", lambda: False)())
        and bool(getattr(input_adapter, "supported", True))
        and _supports_vt(output)
    )
    _tick(dispatch_tick)
    if not interactive:
        dashboard, _ = _dashboard(service, project_id)
        _write(output, render_compact(dashboard, _terminal_size()[0]) + "\n")
        return 0
    selected = 0
    query = ""
    notice = ""
    cursor = None
    history: list[Any] = []
    sort_by = "updated"
    handlers = _install_signal_handlers()
    try:
        with input_adapter:
            _write(output, "\x1b[?1049h")
            while True:
                _tick(dispatch_tick)
                dashboard, next_cursor = _dashboard(service, project_id, selected, query, sort_by, cursor, len(history) + 1)
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
                    if selected >= len(dashboard.tasks) - 1 and next_cursor is not None:
                        history.append(cursor); cursor = next_cursor; selected = 0
                    else:
                        selected = min(max(0, len(dashboard.tasks) - 1), selected + 1)
                    continue
                if action is Action.NEXT_PAGE and next_cursor is not None:
                    history.append(cursor); cursor = next_cursor; selected = 0; continue
                if action is Action.PREVIOUS_PAGE and history:
                    cursor = history.pop(); selected = 0; continue
                if action is Action.SORT:
                    sort_by = {"updated": "subject", "subject": "state", "state": "updated"}[sort_by]
                    notice = "sort: " + sort_by; continue
                if action is Action.SEARCH:
                    reader = getattr(input_adapter, "read_line", None)
                    if callable(reader):
                        query = str(reader("filter: ") or "")
                        selected = 0
                        notice = "filter current page: " + query
                    else:
                        notice = "filter input unavailable"
                    continue
                task = dashboard.selected_task
                if task is None:
                    notice = "no task selected"
                    continue
                if action is Action.VIEW:
                    notice = _call("view", service.show, task.id)
                elif action is Action.CLAIM:
                    notice = _call("claim", service.claim, task.id, actor)
                elif action is Action.RETRY:
                    notice = _call("retry", service.retry_delivery, task.id)
                elif action is Action.OPEN:
                    notice = _call("terminal", service.open_terminal, task.id)
                _tick(dispatch_tick)
    except KeyboardInterrupt:
        return 130
    finally:
        _restore_signal_handlers(handlers)
        if interactive:
            _write(output, "\x1b[?1049l")


def _dashboard(service: Any, project_id: str, selected: int = 0, query: str = "", sort_by: str = "updated", cursor: Any = None, page_number: int = 1) -> tuple[Dashboard, Any]:
    tasks = []
    page = service.board_page(project_id, limit=PAGE_SIZE, cursor=cursor)
    for task in page.tasks:
        value = _as_mapping(task)
        evidence = service.delivery_evidence(str(value.get("id", "")))
        value["delivery"] = _evidence_text(evidence)
        detail_reader = getattr(service, "task_detail", None)
        if callable(detail_reader):
            try:
                detail = detail_reader(str(value.get("id", "")))
                value["dependencies"] = getattr(detail, "dependencies", ())
                value["review_result"] = getattr(detail, "review_result", "")
            except (KeyError, ValueError, RuntimeError):
                pass
        tasks.append(value)
    if sort_by == "subject":
        tasks.sort(key=lambda task: str(task.get("subject", "")).casefold())
    elif sort_by == "state":
        tasks.sort(key=lambda task: (str(task.get("state", "")), str(task.get("id", ""))))
    dashboard = build_dashboard({"agents": tuple(service.agents()), "tasks": tasks, "page_label": "page " + str(page_number) + ("+" if page.next_cursor else ""), "sort_by": sort_by}, selected, query)
    return dashboard, page.next_cursor


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    fields = ("id", "sender", "assignee", "subject", "body", "state", "artifacts")
    return {field: getattr(value, field, "") for field in fields}


def _evidence_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    status = str(getattr(value, "status", value))
    attempts = int(getattr(value, "attempts", 0) or 0)
    detail = sanitize_text(getattr(value, "detail", ""))
    return status + (" ({0} attempts)".format(attempts) if attempts else "") + (": " + detail if detail else "")


def _result(action: str, result: Any) -> str:
    return sanitize_text("{0}: {1}".format(action, result))


def _call(action: str, method: Any, *args: Any) -> str:
    try:
        return _result(action, method(*args))
    except (KeyError, PermissionError, ValueError, RuntimeError, OSError) as error:
        return sanitize_text("{0} failed: {1}".format(action, error))


def _tick(callback: Any) -> None:
    if callable(callback):
        try:
            callback()
        except (RuntimeError, OSError, ValueError):
            pass


def _install_signal_handlers() -> dict[int, Any]:
    if threading.current_thread() is not threading.main_thread():
        return {}
    previous: dict[int, Any] = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, _terminate)
    return previous


def _restore_signal_handlers(previous: Mapping[int, Any]) -> None:
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def _terminate(signum: int, frame: Any) -> None:
    if signum == signal.SIGINT:
        raise KeyboardInterrupt
    raise SystemExit(128 + signum)


def _terminal_size() -> tuple[int, int]:
    size = shutil.get_terminal_size((80, 24))
    return size.columns, size.lines


def _supports_vt(output: Any) -> bool:
    """Honour explicit host capability and conventional non-VT terminal markers."""
    declared = getattr(output, "supports_vt", None)
    if declared is not None:
        return bool(declared)
    term = os.environ.get("TERM", "").lower()
    if term:
        return term != "dumb"
    if os.name != "nt":
        return False
    try:
        import ctypes
        mode = ctypes.c_uint32()
        handle = ctypes.windll.kernel32.GetStdHandle(-11)
        return bool(handle and ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)) and (mode.value & 0x0004))
    except (AttributeError, OSError):
        return False


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
