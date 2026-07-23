"""Claude Code hook session-card adapter."""

from __future__ import annotations

import sys

from .base import HostCapabilities, ManagedJsonAdapter, Surface, _read_json_object, _write_json_object
from ..version import PROTOCOL_VERSION


class ClaudeAdapter(ManagedJsonAdapter):
    name = "claude"
    fixture_suffix = ".json"
    relative_config_path = (".claude", "settings.json")
    relative_marker_path = (".claude", "agent-bridge-host.json")
    mechanism = "hook"

    def capabilities(self) -> HostCapabilities:
        return HostCapabilities(Surface.SESSION_CARD, True, False, True, PROTOCOL_VERSION, "1.0.0")

    def _managed_config(self, root: dict) -> None:
        root.setdefault("hooks", {})

    def _install_config(self) -> None:
        root = _read_json_object(self.config_path)
        hooks = root.setdefault("hooks", {})
        hooks["SessionStart"] = [{"matcher": "", "hooks": [{"type": "command", "command": sys.executable, "args": self._entrypoint()[1:]}]}]
        _write_json_object(self.config_path, root)

    def _uninstall_config(self) -> None:
        root = _read_json_object(self.config_path)
        hooks = root.get("hooks")
        if isinstance(hooks, dict):
            hooks.pop("SessionStart", None)
        _write_json_object(self.config_path, root)

    def _consumer_is_installed(self) -> bool:
        try:
            root = _read_json_object(self.config_path)
            hook = root["hooks"]["SessionStart"][0]["hooks"][0]
        except (KeyError, IndexError, TypeError, ValueError):
            return False
        return hook == {"type": "command", "command": sys.executable, "args": self._entrypoint()[1:]}
