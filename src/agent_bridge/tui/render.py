"""ANSI-free layout generation for the dependency-free terminal UI."""

from __future__ import annotations

import unicodedata
from typing import Iterable

from .model import Dashboard, DashboardTask


def display_width(value: str) -> int:
    """Return terminal cell width without relying on an optional wcwidth package."""
    width = 0
    for character in value:
        if unicodedata.combining(character) or ord(character) < 32 or ord(character) == 127:
            continue
        width += 2 if unicodedata.east_asian_width(character) in ("F", "W") else 1
    return width


def truncate_cells(value: str, width: int) -> str:
    if width <= 0:
        return ""
    if display_width(value) <= width:
        return value
    if width == 1:
        return "…"
    output: list[str] = []
    used = 0
    for character in value:
        character_width = display_width(character)
        if used + character_width > width - 1:
            break
        output.append(character)
        used += character_width
    return "".join(output) + "…"


def render_dashboard(dashboard: Dashboard, width: int, height: int, color: bool = True) -> str:
    """Render a bounded screen; clearing and cursor controls belong to the controller."""
    width = max(20, int(width))
    height = max(4, int(height))
    if width >= 100:
        lines = _wide(dashboard, width)
    else:
        lines = _narrow(dashboard, width)
    lines = [_fit(line, width) for line in lines[:height]]
    return "\n".join(_colorize(line, color) for line in lines)


def render_compact(dashboard: Dashboard, width: int = 80) -> str:
    """Plain, noninteractive fallback for redirected or non-VT output."""
    width = max(20, int(width))
    header = "Agent Bridge tasks ({0})".format(len(dashboard.tasks))
    lines = [header, "ID       STATE              FROM -> TO  SUBJECT"]
    for task in dashboard.tasks:
        source = "{0} -> {1}".format(task.sender, task.assignee)
        lines.append(_columns(("#" + task.id[:8], task.state, source, task.subject), (10, 19, 20, max(1, width - 52))))
    return "\n".join(_fit(line, width) for line in lines)


def _wide(dashboard: Dashboard, width: int) -> list[str]:
    left = max(26, width // 3)
    middle = max(34, width // 3)
    # Two " | " separators consume six terminal cells.
    right = width - left - middle - 6
    lines = [_title(dashboard), _columns(("AGENTS", "TASKS", "DETAILS"), (left, middle, right))]
    agent_lines = ["{0} {1} {2} {3}".format(str(agent.get("name", "?")), str(agent.get("health", "unknown")), str(agent.get("execution_policy", "manual")), ",".join(str(item) for item in agent.get("capabilities", ()))) for agent in dashboard.agents] or ["(none)"]
    task_lines = [_task_line(task, index == dashboard.selected, middle) for index, task in enumerate(dashboard.tasks)] or ["(none)"]
    detail_lines = _details(dashboard.selected_task, right)
    for row in range(max(len(agent_lines), len(task_lines), len(detail_lines))):
        lines.append(_columns((_at(agent_lines, row), _at(task_lines, row), _at(detail_lines, row)), (left, middle, right)))
    return lines + [_help()]


def _narrow(dashboard: Dashboard, width: int) -> list[str]:
    agents = "; ".join("{0} {1} {2} {3}".format(agent.get("name", "?"), agent.get("health", "unknown"), agent.get("execution_policy", "manual"), ",".join(agent.get("capabilities", ()))) for agent in dashboard.agents)
    lines = [_title(dashboard), "Agents: " + agents, "Tasks"]
    lines.extend(_task_line(task, index == dashboard.selected, width) for index, task in enumerate(dashboard.tasks))
    lines.append("Details")
    lines.extend(_details(dashboard.selected_task, width))
    lines.append(_help())
    return lines


def _title(dashboard: Dashboard) -> str:
    counts = dashboard.counts
    return "Agent Bridge {5} sort:{6} | inbox {0} working {1} review {2} completed {3} failed {4}".format(
        counts.inbox, counts.working, counts.review, counts.completed, counts.failed, dashboard.page_label, dashboard.sort_by,
    )


def _task_line(task: DashboardTask, selected: bool, width: int) -> str:
    marker = ">" if selected else " "
    prefix = "{0} #{1} {2} {3}→{4} {5}: ".format(marker, task.id[:8], task.state, task.sender, task.assignee, task.delivery)
    return prefix + truncate_cells(task.subject, max(1, width - display_width(prefix)))


def _details(task: DashboardTask | None, width: int) -> list[str]:
    if task is None:
        return ["(select a task)"]
    values = [
        "#{0}".format(task.id),
        "from {0} to {1}".format(task.sender, task.assignee),
        "delivery: {0}".format(task.delivery or "unknown"),
        "body: {0}".format(task.body.replace("\n", " ")),
    ]
    if task.artifacts:
        values.append("artifacts: " + ", ".join(task.artifacts))
    if task.dependencies:
        values.append("depends: " + ", ".join(task.dependencies))
    if task.review_result:
        values.append("review: " + task.review_result)
    return [truncate_cells(value, width) for value in values]


def _columns(values: Iterable[str], widths: tuple[int, ...]) -> str:
    padded = []
    for value, width in zip(values, widths):
        clipped = truncate_cells(value, width)
        padded.append(clipped + " " * max(0, width - display_width(clipped)))
    return " | ".join(padded)


def _fit(value: str, width: int) -> str:
    return truncate_cells(value, width)


def _at(values: list[str], index: int) -> str:
    return values[index] if index < len(values) else ""


def _help() -> str:
    return "↑/↓ select  n/p PgDn/PgUp page  s page sort  / page filter  c claim  r retry  o terminal  q quit"


def _colorize(line: str, color: bool) -> str:
    if not color:
        return line
    if line.startswith(">"):
        return "\x1b[7m" + line + "\x1b[0m"
    if line in ("Tasks", "Details") or line.startswith("Agent Bridge"):
        return "\x1b[1m" + line + "\x1b[0m"
    return line
