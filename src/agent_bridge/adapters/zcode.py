"""ZCode plugin hook session-card adapter."""

from __future__ import annotations

from .base import HostCapabilities, ManagedJsonAdapter, Surface
from ..version import PROTOCOL_VERSION


class ZCodeAdapter(ManagedJsonAdapter):
    name = "zcode"
    fixture_suffix = ".json"
    relative_config_path = (".zcode", "cli", "config.json")

    def capabilities(self) -> HostCapabilities:
        return HostCapabilities(Surface.SESSION_CARD, True, False, True, PROTOCOL_VERSION, "1.0.0")

    def _managed_config(self, root: dict) -> None:
        plugins = root.setdefault("plugins", {})
        if not isinstance(plugins, dict):
            raise ValueError("zcode plugins config must be an object")
        enabled = plugins.setdefault("enabledPlugins", {})
        if not isinstance(enabled, dict):
            raise ValueError("zcode enabledPlugins config must be an object")
        enabled["agent-bridge@local"] = True

    def _remove_legacy_managed(self, root: dict) -> None:
        plugins = root.get("plugins")
        if isinstance(plugins, dict):
            enabled = plugins.get("enabledPlugins")
            if isinstance(enabled, dict):
                enabled.pop("agent-bridge@local", None)
