"""ZCode plugin hook session-card adapter."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from .base import HostCapabilities, ManagedJsonAdapter, Surface, _atomic_write, _optimistic_json_update, _read_json_object
from ..version import PROTOCOL_VERSION


class ZCodeOwnershipConflict(ValueError):
    """A local plugin bundle exists but was not created by this integration."""


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

    @property
    def ownership_path(self) -> Path:
        return self.home / ".agent-bridge" / "host-integrations" / "zcode-ownership.json"

    def _install_config(self) -> None:
        if self.ownership_path.exists():
            ownership = _read_json_object(self.ownership_path)
        else:
            if self.plugin_bundle_path.exists():
                raise ZCodeOwnershipConflict("refusing to overwrite an unowned ZCode plugin bundle")
            root_before = _read_json_object(self.config_path)
            plugins_before = root_before.get("plugins", {}) if isinstance(root_before.get("plugins", {}), dict) else {}
            enabled_before = plugins_before.get("enabledPlugins", {}) if isinstance(plugins_before.get("enabledPlugins", {}), dict) else {}
            local_before = plugins_before.get("localPlugins", {}) if isinstance(plugins_before.get("localPlugins", {}), dict) else {}
            ownership = {"enabled_present": "agent-bridge@local" in enabled_before, "enabled": enabled_before.get("agent-bridge@local"), "local_present": "agent-bridge@local" in local_before, "local": local_before.get("agent-bridge@local"), "bundle_existed": False}
            self.ownership_path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(self.ownership_path, json.dumps(ownership, sort_keys=True) + "\n")
        self._assert_contained(self.plugin_bundle_path)
        self.plugin_bundle_path.mkdir(parents=True, exist_ok=True)
        source = Path(__file__).resolve().parents[3] / "integrations" / "zcode" / "plugin.json"
        shutil.copyfile(source, self.plugin_bundle_path / "plugin.json")
        plugin = json.loads((self.plugin_bundle_path / "plugin.json").read_text(encoding="utf-8"))
        plugin["command"] = sys.executable
        plugin["args"] = self._entrypoint()[1:]
        _atomic_write(self.plugin_bundle_path / "plugin.json", json.dumps(plugin, ensure_ascii=False, indent=2) + "\n")
        # Retain the Task8 ZCode hook layout while the v2 consumer uses the
        # versioned session-card manifest above.  It is owned beside the v2
        # bundle and removed with it.
        legacy_hooks = self.plugin_bundle_path.parent / "1.3.0" / "hooks"
        legacy_hooks.mkdir(parents=True, exist_ok=True)
        _atomic_write(legacy_hooks / "hooks.json", json.dumps({"hooks": {}}, sort_keys=True) + "\n")
        def update(root: dict) -> None:
            plugins = root.setdefault("plugins", {})
            if not isinstance(plugins, dict):
                raise ValueError("zcode plugins config must be an object")
            enabled = plugins.setdefault("enabledPlugins", {})
            local = plugins.setdefault("localPlugins", {})
            if not isinstance(enabled, dict) or not isinstance(local, dict):
                raise ValueError("zcode plugin registrations must be objects")
            enabled["agent-bridge@local"] = True
            local["agent-bridge@local"] = str(self.plugin_bundle_path)
            root["agent_bridge"] = self._managed_metadata()
        _optimistic_json_update(self.config_path, update)

    def _consumer_is_installed(self) -> bool:
        try:
            if self._managed_config_text() is None:
                return False
            self._assert_contained(self.plugin_bundle_path)
            if self.plugin_bundle_path.is_symlink():
                return False
            root = _read_json_object(self.config_path)
            bundle = Path(root["plugins"]["localPlugins"]["agent-bridge@local"])
            plugin = json.loads((bundle / "plugin.json").read_text(encoding="utf-8"))
        except (KeyError, OSError, ValueError, TypeError):
            return False
        return (
            bundle == self.plugin_bundle_path
            and root.get("agent_bridge") == self._managed_metadata()
            and plugin.get("command") == sys.executable
            and plugin.get("args") == self._entrypoint()[1:]
        )

    def _uninstall_config(self) -> None:
        try:
            ownership = _read_json_object(self.ownership_path)
        except ValueError:
            ownership = {}
        def update(root: dict) -> None:
            plugins = root.get("plugins")
            if isinstance(plugins, dict):
                enabled = plugins.get("enabledPlugins")
                if isinstance(enabled, dict):
                    if enabled.get("agent-bridge@local") is True:
                        if ownership.get("enabled_present"):
                            enabled["agent-bridge@local"] = ownership.get("enabled")
                        else:
                            enabled.pop("agent-bridge@local", None)
                local = plugins.get("localPlugins")
                if isinstance(local, dict):
                    if local.get("agent-bridge@local") == str(self.plugin_bundle_path):
                        if ownership.get("local_present"):
                            local["agent-bridge@local"] = ownership.get("local")
                        else:
                            local.pop("agent-bridge@local", None)
            if root.get("agent_bridge") == self._managed_metadata():
                root.pop("agent_bridge", None)
        _optimistic_json_update(self.config_path, update)
        self._assert_contained(self.plugin_bundle_path)
        parent = self.plugin_bundle_path.parent
        if not ownership.get("bundle_existed"):
            # These are the only version directories this adapter creates.
            # Never sweep an unknown sibling: it may be a user plugin.
            for version in ("1.0.0", "1.3.0"):
                owned_version = parent / version
                if owned_version.is_dir() and not owned_version.is_symlink():
                    shutil.rmtree(owned_version)
            if parent.is_dir() and not parent.is_symlink() and not any(parent.iterdir()):
                parent.rmdir()
        try:
            self.ownership_path.unlink()
        except OSError:
            pass

    def _remove_legacy_managed(self, root: dict) -> None:
        plugins = root.get("plugins")
        if isinstance(plugins, dict):
            enabled = plugins.get("enabledPlugins")
            if isinstance(enabled, dict):
                enabled.pop("agent-bridge@local", None)
