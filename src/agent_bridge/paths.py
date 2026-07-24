"""Data-root handling shared by Agent Bridge v2 entry points."""

import os
from pathlib import Path
from typing import Mapping


def get_data_root(env: Mapping[str, str]) -> Path:
    """Return the configured data root without requiring it to exist."""
    root = Path(env.get("AGENT_BRIDGE_HOME", str(Path.home() / ".agent-bridge")))
    return require_local_data_root(root)


def require_local_data_root(path: Path) -> Path:
    """Resolve a data root and reject Windows UNC network shares."""
    raw_path = str(path)
    if os.name == "nt" and raw_path.replace("/", "\\").startswith("\\\\"):
        raise ValueError("Agent Bridge data root must be on a local filesystem")
    return path.expanduser().resolve()
