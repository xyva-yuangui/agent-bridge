"""POSIX raw-terminal input with guaranteed attribute restoration."""

from __future__ import annotations

import select
import sys
from typing import Any, Optional

from .input_common import Action


def parse_key(value: str) -> Optional[Action]:
    return {
        "\x1b[A": Action.UP, "\x1b[B": Action.DOWN, "\r": Action.VIEW, "\n": Action.VIEW,
        "c": Action.CLAIM, "r": Action.RETRY, "o": Action.OPEN, "/": Action.SEARCH,
        "q": Action.QUIT, "\x03": Action.QUIT,
    }.get(value)


class PosixInputAdapter:
    """Read one key without leaving a tty in raw mode if rendering raises."""

    def __init__(self, stream: Any = None, *, termios_module: Any = None, select_fn: Any = None) -> None:
        self.stream = stream if stream is not None else sys.stdin
        self._termios = termios_module
        self._select = select_fn or select.select
        self._saved: Any = None

    def __enter__(self) -> "PosixInputAdapter":
        if not self.supported:
            return self
        termios = self._load_termios()
        self._saved = termios.tcgetattr(self.stream.fileno())
        current = list(self._saved)
        current[3] &= ~(termios.ECHO | termios.ICANON)
        termios.tcsetattr(self.stream.fileno(), termios.TCSANOW, current)
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        if self._saved is not None:
            self._load_termios().tcsetattr(self.stream.fileno(), self._load_termios().TCSANOW, self._saved)
            self._saved = None
        return False

    @property
    def supported(self) -> bool:
        return bool(getattr(self.stream, "isatty", lambda: False)())

    def read_key(self, timeout: float) -> Optional[Action]:
        if not self.supported:
            return None
        ready, _, _ = self._select([self.stream], [], [], timeout)
        if not ready:
            return None
        first = self.stream.read(1)
        if first != "\x1b":
            return parse_key(first)
        # ANSI arrows arrive as ESC [ A/B.  A short zero-time probe keeps ESC usable.
        ready, _, _ = self._select([self.stream], [], [], 0.01)
        if not ready:
            return None
        return parse_key(first + self.stream.read(2))

    def read_line(self, prompt: str = "") -> str:
        """Collect a short filter in raw mode; rendering owns any visible prompt."""
        values: list[str] = []
        while len(values) < 256:
            character = self.stream.read(1)
            if character in ("", "\r", "\n"):
                break
            if character in ("\x08", "\x7f"):
                if values:
                    values.pop()
            elif character.isprintable():
                values.append(character)
        return "".join(values)

    def _load_termios(self) -> Any:
        if self._termios is None:
            import termios
            self._termios = termios
        return self._termios
