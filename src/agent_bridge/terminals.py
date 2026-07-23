"""Safe terminal-opening fallbacks for task inspection."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Tuple, Union


@dataclass(frozen=True)
class OpenResult:
    opened: bool
    method: str
    argv: Tuple[str, ...] = ()
    instructions: str = ""
    pid: Optional[int] = None


def open_task_terminal(adapter: Any, task_id: str, workspace: Union[Path, str]) -> OpenResult:
    """Prefer a host terminal and otherwise use platform-safe argv fallbacks."""
    if not isinstance(task_id, str) or not task_id or "\x00" in task_id:
        raise ValueError("task ID must be a non-empty string")
    cwd = Path(workspace).expanduser().resolve()
    if not cwd.is_dir():
        raise ValueError("workspace must be an existing directory")
    command = (sys.executable, "-m", "agent_bridge.cli", "show", task_id)
    host_result = _open_host_terminal(adapter, command, str(cwd))
    if host_result is not None:
        return host_result
    if _is_windows():
        argv = ("wt.exe", "-d", str(cwd), "--", *command)
        return _open_process(argv, cwd, "windows-terminal")
    if _is_macos():
        # ``open --args`` keeps task text as data.  Do not interpolate it into
        # AppleScript source or a shell command.
        argv = ("open", "-a", "Terminal", "--args", *command)
        return _open_process(argv, cwd, "macos-terminal")
    return OpenResult(
        False,
        "instructions",
        command,
        "Open a terminal in {0} and run: {1}".format(str(cwd), " ".join(command)),
    )


def _open_host_terminal(adapter: Any, argv: Tuple[str, ...], workspace: str) -> Optional[OpenResult]:
    if adapter is None or getattr(adapter, "supports_integrated_terminal", False) is not True:
        return None
    opener = getattr(adapter, "open_integrated_terminal", None)
    if not callable(opener):
        return None
    try:
        opened = opener(argv, workspace)
    except OSError:
        return None
    if isinstance(opened, OpenResult):
        return opened
    if opened:
        return OpenResult(True, "host", argv)
    return None


def _open_process(argv: Tuple[str, ...], cwd: Path, method: str) -> OpenResult:
    kwargs: dict[str, Any] = {
        "cwd": str(cwd),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "shell": False,
    }
    if _is_windows():
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(list(argv), **kwargs)
    except OSError:
        return OpenResult(False, "instructions", argv, "Open a terminal in {0} and inspect task {1}.".format(cwd, argv[-1]))
    return OpenResult(True, method, argv, pid=process.pid)


def _is_windows() -> bool:
    return os.name == "nt"


def _is_macos() -> bool:
    return sys.platform == "darwin"
