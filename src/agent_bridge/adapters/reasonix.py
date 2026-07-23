"""Reasonix plugin session-card adapter."""

from __future__ import annotations

import json
import sys

from .base import HostCapabilities, ManagedTomlAdapter, Surface, _append_block, _atomic_write, _remove_toml_block
from ..version import PROTOCOL_VERSION


class ReasonixAdapter(ManagedTomlAdapter):
    name = "reasonix"
    fixture_suffix = ".toml"
    relative_config_path = (".reasonix", "config.toml")
    relative_marker_path = (".reasonix", "agent-bridge-host.json")
    mechanism = "plugin"

    def capabilities(self) -> HostCapabilities:
        return HostCapabilities(Surface.SESSION_CARD, True, False, True, PROTOCOL_VERSION, "1.0.0")

    def _install_config(self) -> None:
        source = self.config_path.read_text(encoding="utf-8") if self.config_path.exists() else ""
        body = "\n".join((
            "[[plugins]]", "name = \"agent-bridge\"", "command = {0}".format(json.dumps(sys.executable)),
            "args = {0}".format(json.dumps(self._entrypoint()[1:])),
        ))
        block = "# >>> agent-bridge:reasonix >>>\n{0}\n# <<< agent-bridge:reasonix <<<\n".format(body)
        _atomic_write(self.config_path, _append_block(_remove_toml_block(source, self.name), block))

    def _consumer_is_installed(self) -> bool:
        try:
            text = self.config_path.read_text(encoding="utf-8")
        except OSError:
            return False
        return "[[plugins]]" in text and "name = \"agent-bridge\"" in text and "serve" in text
