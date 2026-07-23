"""Claude Code hook session-card adapter."""

from __future__ import annotations

import sys

from .base import HostCapabilities, ManagedJsonAdapter, Surface, _optimistic_json_update, _read_json_object
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
        def update(root: dict) -> None:
            hooks = root.setdefault("hooks", {})
            if not isinstance(hooks, dict):
                raise ValueError("claude hooks config must be an object")
            session_start = hooks.setdefault("SessionStart", [])
            if not isinstance(session_start, list):
                raise ValueError("claude SessionStart hooks must be a list")
            managed = self._managed_hook()
            hooks["SessionStart"] = [item for item in session_start if item != managed] + [managed]
            root["agent_bridge"] = self._managed_metadata()
        _optimistic_json_update(self.config_path, update)

    def _uninstall_config(self) -> None:
        def update(root: dict) -> None:
            hooks = root.get("hooks")
            if isinstance(hooks, dict) and isinstance(hooks.get("SessionStart"), list):
                remaining = [item for item in hooks["SessionStart"] if item != self._managed_hook()]
                if remaining:
                    hooks["SessionStart"] = remaining
                else:
                    hooks.pop("SessionStart", None)
            if root.get("agent_bridge") == self._managed_metadata():
                root.pop("agent_bridge", None)
        _optimistic_json_update(self.config_path, update)

    def _consumer_is_installed(self) -> bool:
        try:
            if self._managed_config_text() is None:
                return False
            root = _read_json_object(self.config_path)
            hooks = root["hooks"]["SessionStart"]
        except (KeyError, IndexError, TypeError, ValueError):
            return False
        return root.get("agent_bridge") == self._managed_metadata() and self._managed_hook() in hooks

    def _managed_hook(self) -> dict:
        return {"matcher": "", "hooks": [{"type": "command", "command": sys.executable, "args": self._entrypoint()[1:]}]}
