"""ZCode plugin hook session-card adapter."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from .base import HostCapabilities, ManagedJsonAdapter, Surface, _read_json_object, _write_json_object
from ..version import PROTOCOL_VERSION


class ZCodeAdapter(ManagedJsonAdapter):
    name = "zcode"
    fixture_suffix = ".json"
    relative_config_path = (".zcode", "cli", "config.json")
    relative_marker_path = (".zcode", "cli", "agent-bridge-host.json")
    mechanism = "plugin_hook"

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

    @property
    def plugin_bundle_path(self) -> Path:
        return self.home / ".zcode" / "cli" / "plugins" / "cache" / "local" / "agent-bridge" / "1.0.0"

    def _install_config(self) -> None:
        self.plugin_bundle_path.mkdir(parents=True, exist_ok=True)
        source = Path(__file__).resolve().parents[3] / "integrations" / "zcode" / "plugin.json"
        shutil.copyfile(source, self.plugin_bundle_path / "plugin.json")
        plugin = json.loads((self.plugin_bundle_path / "plugin.json").read_text(encoding="utf-8"))
        plugin["command"] = sys.executable
        plugin["args"] = self._entrypoint()[1:]
        _write_json_object(self.plugin_bundle_path / "plugin.json", plugin)
        root = _read_json_object(self.config_path)
        plugins = root.setdefault("plugins", {})
        plugins.setdefault("enabledPlugins", {})["agent-bridge@local"] = True
        plugins.setdefault("localPlugins", {})["agent-bridge@local"] = str(self.plugin_bundle_path)
        _write_json_object(self.config_path, root)

    def _consumer_is_installed(self) -> bool:
        try:
            root = _read_json_object(self.config_path)
            bundle = Path(root["plugins"]["localPlugins"]["agent-bridge@local"])
            plugin = json.loads((bundle / "plugin.json").read_text(encoding="utf-8"))
        except (KeyError, OSError, ValueError, TypeError):
            return False
        return bundle == self.plugin_bundle_path and plugin.get("command") == sys.executable and plugin.get("args") == self._entrypoint()[1:]

    def _uninstall_config(self) -> None:
        root = _read_json_object(self.config_path)
        plugins = root.get("plugins")
        if isinstance(plugins, dict):
            enabled = plugins.get("enabledPlugins")
            if isinstance(enabled, dict):
                enabled.pop("agent-bridge@local", None)
            local = plugins.get("localPlugins")
            if isinstance(local, dict):
                local.pop("agent-bridge@local", None)
        _write_json_object(self.config_path, root)
        if self.plugin_bundle_path.exists():
            shutil.rmtree(self.plugin_bundle_path)

    def _remove_legacy_managed(self, root: dict) -> None:
        plugins = root.get("plugins")
        if isinstance(plugins, dict):
            enabled = plugins.get("enabledPlugins")
            if isinstance(enabled, dict):
                enabled.pop("agent-bridge@local", None)
