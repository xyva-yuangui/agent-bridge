"""Claude Code hook session-card adapter."""

from __future__ import annotations

from .base import HostCapabilities, ManagedJsonAdapter, Surface
from ..version import PROTOCOL_VERSION


class ClaudeAdapter(ManagedJsonAdapter):
    name = "claude"
    fixture_suffix = ".json"
    relative_config_path = (".claude", "settings.json")

    def capabilities(self) -> HostCapabilities:
        return HostCapabilities(Surface.SESSION_CARD, True, False, True, PROTOCOL_VERSION, "1.0.0")

    def _managed_config(self, root: dict) -> None:
        root.setdefault("hooks", {})
