"""Codex MCP session-card adapter."""

from __future__ import annotations

from .base import HostCapabilities, ManagedTomlAdapter, Surface
from ..version import PROTOCOL_VERSION


class CodexAdapter(ManagedTomlAdapter):
    name = "codex"
    fixture_suffix = ".toml"
    relative_config_path = (".codex", "config.toml")
    relative_marker_path = (".codex", "agent-bridge-host.json")
    mechanism = "mcp"
    managed_table = "mcp_servers.agent_bridge"

    def capabilities(self) -> HostCapabilities:
        return HostCapabilities(Surface.SESSION_CARD, True, False, True, PROTOCOL_VERSION, "1.0.0")
