"""Desktop host adapter registry."""

from __future__ import annotations

from pathlib import Path
from typing import Tuple, Type

from .base import HOST_IDENTITIES, HostAdapter, canonical_host_name
from .claude import ClaudeAdapter
from .codex import CodexAdapter
from .reasonix import ReasonixAdapter
from .zcode import ZCodeAdapter


ADAPTER_TYPES: Tuple[Type[HostAdapter], ...] = (
    CodexAdapter,
    ClaudeAdapter,
    ReasonixAdapter,
    ZCodeAdapter,
)
_ADAPTER_BY_NAME = {adapter_type.name: adapter_type for adapter_type in ADAPTER_TYPES}
if set(_ADAPTER_BY_NAME) != {identity.name for identity in HOST_IDENTITIES}:
    raise RuntimeError("adapter registry is out of sync with host identities")


def adapter_for(name: str, home: Path) -> HostAdapter:
    return _ADAPTER_BY_NAME[canonical_host_name(name)](home)


__all__ = (
    "ADAPTER_TYPES",
    "ClaudeAdapter",
    "CodexAdapter",
    "ReasonixAdapter",
    "ZCodeAdapter",
    "adapter_for",
)
