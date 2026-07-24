"""Windows console input with no dependency beyond the standard library."""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

from .input_common import Action


def parse_windows_key(first: str, second: str = "") -> Optional[Action]:
    if first in ("\x00", "\xe0"):
        return {"H": Action.UP, "P": Action.DOWN}.get(second)
    return {
        "\r": Action.VIEW, "c": Action.CLAIM, "r": Action.RETRY, "o": Action.OPEN,
        "/": Action.SEARCH, "n": Action.NEXT_PAGE, "p": Action.PREVIOUS_PAGE, "s": Action.SORT, "q": Action.QUIT, "\x03": Action.QUIT,
    }.get(first)


class WindowsInputAdapter:
    """Use msvcrt if available and restore a supplied/native console mode in finally."""

    def __init__(self, *, msvcrt_module: Any = None, console: Any = None, sleep_fn: Callable[[float], None] = time.sleep) -> None:
        self._msvcrt = msvcrt_module
        self.console = console
        self._sleep = sleep_fn
        self._saved_mode: Any = None

    def __enter__(self) -> "WindowsInputAdapter":
        if self.console is not None:
            self._saved_mode = self.console.get_mode()
            # Disable line and echo input in the injectable/native console abstraction.
            self.console.set_mode(self._saved_mode & ~0x0006)
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        if self.console is not None and self._saved_mode is not None:
            self.console.set_mode(self._saved_mode)
            self._saved_mode = None
        return False

    @property
    def supported(self) -> bool:
        return self._load_msvcrt() is not None

    def read_key(self, timeout: float) -> Optional[Action]:
        msvcrt = self._load_msvcrt()
        if msvcrt is None:
            return None
        if not msvcrt.kbhit():
            self._sleep(max(0.0, min(0.5, timeout)))
            return None
        first = msvcrt.getwch()
        return parse_windows_key(first, msvcrt.getwch() if first in ("\x00", "\xe0") else "")

    def read_line(self, prompt: str = "") -> str:
        """Collect a bounded filter while the console is in the adapter's input mode."""
        msvcrt = self._load_msvcrt()
        if msvcrt is None:
            return ""
        values: list[str] = []
        while len(values) < 256:
            character = msvcrt.getwch()
            if character in ("\r", "\n"):
                break
            if character in ("\x08", "\x7f"):
                if values:
                    values.pop()
            elif character.isprintable():
                values.append(character)
        return "".join(values)

    def _load_msvcrt(self) -> Any:
        if self._msvcrt is not None:
            return self._msvcrt
        try:
            import msvcrt
        except ImportError:
            return None
        self._msvcrt = msvcrt
        return msvcrt
