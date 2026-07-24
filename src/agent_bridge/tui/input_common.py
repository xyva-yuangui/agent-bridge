"""The small shared vocabulary emitted by platform key readers."""

from __future__ import annotations

import enum


class Action(str, enum.Enum):
    UP = "up"
    DOWN = "down"
    VIEW = "view"
    CLAIM = "claim"
    RETRY = "retry"
    OPEN = "open"
    SEARCH = "search"
    QUIT = "quit"
