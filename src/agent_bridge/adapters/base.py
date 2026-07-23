"""Typed contracts and safe configuration helpers for desktop host adapters."""

from __future__ import annotations

import abc
import base64
import enum
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

from ..models import DeliveryStatus
from ..version import PROTOCOL_VERSION


class Surface(str, enum.Enum):
    NATIVE_PANEL = "native_panel"
    SESSION_CARD = "session_card"
    TERMINAL_FALLBACK = "terminal_fallback"


@dataclass(frozen=True)
class HostCapabilities:
    surface: Surface
    can_ack: bool
    can_open_terminal: bool
    can_receive_context: bool
    protocol_version: int
    integration_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.protocol_version, int) or self.protocol_version < 1:
            raise ValueError("protocol_version must be an integer greater than zero")
        if not re.fullmatch(r"\d+\.\d+\.\d+", self.integration_version):
            raise ValueError("integration_version must be a semantic version")


@dataclass(frozen=True)
class HostDetection:
    host: str
    found: bool
    config_path: Path
    detail: str


@dataclass(frozen=True)
class InstallPlan:
    host: str
    config_path: Path
    changes: Tuple[str, ...]
    warning: str = ""


@dataclass(frozen=True)
class OperationResult:
    host: str
    ok: bool
    status: DeliveryStatus
    message: str


@dataclass(frozen=True)
class HealthCheck:
    host: str
    ok: bool
    capabilities: HostCapabilities
    warning: str = ""


@dataclass(frozen=True)
class TaskCard:
    task_id: str
    subject: str
    body: str

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id or len(self.task_id) > 128 or "\x00" in self.task_id:
            raise ValueError("task_id must be a non-empty bounded string")
        if not isinstance(self.subject, str) or not self.subject or len(self.subject) > 256 or "\x00" in self.subject:
            raise ValueError("subject must be a non-empty bounded string")
        if not isinstance(self.body, str) or len(self.body) > 8192 or "\x00" in self.body:
            raise ValueError("body must be a bounded string")


@dataclass(frozen=True)
class HostIdentity:
    name: str
    aliases: Tuple[str, ...] = ()


# The only source of truth for public host names and accepted aliases.
HOST_IDENTITIES = (
    HostIdentity("codex", ("openai-codex",)),
    HostIdentity("claude", ("claude-code",)),
    HostIdentity("reasonix", ()),
    HostIdentity("zcode", ("z-code",)),
)


def canonical_host_name(value: str) -> str:
    normalized = str(value).strip().lower()
    for identity in HOST_IDENTITIES:
        if normalized == identity.name or normalized in identity.aliases:
            return identity.name
    raise KeyError("unknown host: {0}".format(value))


Acknowledgement = Callable[..., None]


class HostAdapter(abc.ABC):
    """Strict host contract; configuration writes are limited to managed data."""

    name: str
    fixture_suffix: str

    def __init__(self, home: Path) -> None:
        self.home = Path(home)

    @property
    @abc.abstractmethod
    def config_path(self) -> Path:
        """The documented host configuration file."""

    @abc.abstractmethod
    def capabilities(self) -> HostCapabilities:
        """Return actual capabilities, never aspirational ones."""

    @abc.abstractmethod
    def _host_present(self) -> bool:
        """Whether the host's documented configuration location exists."""

    @abc.abstractmethod
    def _install_config(self) -> None:
        """Apply only this integration's managed configuration."""

    @abc.abstractmethod
    def _uninstall_config(self) -> None:
        """Remove only this integration's managed configuration."""

    def detect(self) -> HostDetection:
        found = self._host_present()
        return HostDetection(
            self.name,
            found,
            self.config_path,
            "configuration location found" if found else "configuration location is absent",
        )

    def plan_install(self) -> InstallPlan:
        warning = "" if self.detect().found else "host is not detected; install creates its documented configuration"
        return InstallPlan(self.name, self.config_path, ("install managed integration",), warning)

    def install(self, plan: Optional[InstallPlan] = None) -> OperationResult:
        actual_plan = plan or self.plan_install()
        if actual_plan.host != self.name or actual_plan.config_path != self.config_path:
            raise ValueError("install plan does not belong to this host")
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self._install_config()
        return OperationResult(self.name, True, DeliveryStatus.QUEUED, "managed integration installed")

    def uninstall(self) -> OperationResult:
        if not self.config_path.exists():
            return OperationResult(self.name, True, DeliveryStatus.QUEUED, "no managed integration was installed")
        self._uninstall_config()
        return OperationResult(self.name, True, DeliveryStatus.QUEUED, "managed integration removed")

    def health_check(self) -> HealthCheck:
        if self.detect().found:
            return HealthCheck(self.name, True, self.capabilities())
        return HealthCheck(
            self.name,
            False,
            HostCapabilities(Surface.TERMINAL_FALLBACK, False, False, False, PROTOCOL_VERSION, self.capabilities().integration_version),
            "host integration is unavailable; use the terminal fallback",
        )

    def notify_in_app(self, task: TaskCard, acknowledge: Optional[Acknowledgement] = None) -> OperationResult:
        if not isinstance(task, TaskCard):
            raise TypeError("task must be a TaskCard")
        detection = self.detect()
        capabilities = self.capabilities()
        if not detection.found:
            return OperationResult(self.name, False, DeliveryStatus.FAILED, "host is not detected; task was not delivered")
        if capabilities.surface == Surface.TERMINAL_FALLBACK or not capabilities.can_receive_context:
            return OperationResult(self.name, False, DeliveryStatus.FAILED, "host has no in-application task-card surface")
        if acknowledge is not None and capabilities.can_ack:
            try:
                acknowledge(
                    host_identity=self.name,
                    task_id=task.task_id,
                    integration_version=capabilities.integration_version,
                    protocol_version=capabilities.protocol_version,
                )
            except Exception as error:
                return OperationResult(self.name, False, DeliveryStatus.FAILED, "acknowledgement failed: {0}".format(error))
        return OperationResult(self.name, True, DeliveryStatus.PLUGIN_DELIVERED, "task card accepted by host integration")

    def launch(self, task: TaskCard) -> OperationResult:
        if not self.detect().found:
            return OperationResult(self.name, False, DeliveryStatus.FAILED, "host is not detected; launch was not attempted")
        return OperationResult(self.name, False, DeliveryStatus.FAILED, "host integration does not expose process launch")

    def open_terminal(self, task: TaskCard) -> OperationResult:
        if not self.detect().found:
            return OperationResult(self.name, False, DeliveryStatus.FAILED, "host is not detected; terminal was not opened")
        return OperationResult(self.name, False, DeliveryStatus.FAILED, "use the platform terminal fallback")


class ManagedTomlAdapter(HostAdapter):
    """Managed TOML block support that leaves all unowned text untouched."""

    relative_config_path: Tuple[str, ...]

    @property
    def config_path(self) -> Path:
        return self.home.joinpath(*self.relative_config_path)

    def _host_present(self) -> bool:
        return self.config_path.parent.is_dir()

    def _install_config(self) -> None:
        source = self.config_path.read_text(encoding="utf-8") if self.config_path.exists() else ""
        cleaned = _remove_toml_block(source, self.name)
        body = "integration_version = {0}\nprotocol_version = {1}\nsurface = \"session_card\"".format(
            json.dumps(self.capabilities().integration_version), self.capabilities().protocol_version
        )
        block = "# >>> agent-bridge:{0} >>>\n{1}\n# <<< agent-bridge:{0} <<<\n".format(self.name, body)
        self.config_path.write_text(_append_block(cleaned, block), encoding="utf-8")

    def _uninstall_config(self) -> None:
        source = self.config_path.read_text(encoding="utf-8")
        self.config_path.write_text(_remove_toml_block(source, self.name), encoding="utf-8")


class ManagedJsonAdapter(HostAdapter):
    """JSON integration storage with a byte-exact restore of existing config."""

    relative_config_path: Tuple[str, ...]

    @property
    def config_path(self) -> Path:
        return self.home.joinpath(*self.relative_config_path)

    def _host_present(self) -> bool:
        return self.config_path.parent.is_dir()

    def _managed_config(self, root: dict) -> None:
        """Host-specific documented configuration mutation."""

    def _install_config(self) -> None:
        original = self.config_path.read_text(encoding="utf-8") if self.config_path.exists() else "{}\n"
        try:
            root = json.loads(original)
        except json.JSONDecodeError as error:
            raise ValueError("cannot update invalid JSON host config") from error
        if not isinstance(root, dict):
            raise ValueError("host config must contain a JSON object")
        existing = root.get("agent_bridge")
        saved_original = _original_json_text(existing)
        if saved_original is not None:
            original_text = saved_original
        elif existing is not None:
            # Older managed JSON did not include a byte snapshot. Remove that
            # owned data before recording a clean baseline for this repair.
            root.pop("agent_bridge", None)
            self._remove_legacy_managed(root)
            original_text = json.dumps(root, ensure_ascii=False, indent=2) + "\n"
        else:
            # A fresh install keeps the complete original so uninstall can
            # restore unrelated JSON byte-for-byte.
            original_text = original
        self._managed_config(root)
        root["agent_bridge"] = {
            "integration_version": self.capabilities().integration_version,
            "protocol_version": self.capabilities().protocol_version,
            "surface": Surface.SESSION_CARD.value,
            "original_config": base64.b64encode(original_text.encode("utf-8")).decode("ascii"),
        }
        self.config_path.write_text(json.dumps(root, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _uninstall_config(self) -> None:
        source = self.config_path.read_text(encoding="utf-8")
        try:
            root = json.loads(source)
        except json.JSONDecodeError as error:
            raise ValueError("cannot update invalid JSON host config") from error
        managed = root.get("agent_bridge") if isinstance(root, dict) else None
        original_text = _original_json_text(managed)
        if original_text is not None:
            self.config_path.write_text(original_text, encoding="utf-8")
            return
        if isinstance(root, dict):
            root.pop("agent_bridge", None)
            self._remove_legacy_managed(root)
            self.config_path.write_text(json.dumps(root, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _remove_legacy_managed(self, root: dict) -> None:
        """Remove pre-snapshot data owned by a host integration."""


def _original_json_text(managed: object) -> Optional[str]:
    if not isinstance(managed, dict) or not isinstance(managed.get("original_config"), str):
        return None
    try:
        return base64.b64decode(managed["original_config"].encode("ascii"), validate=True).decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return None


def _remove_toml_block(source: str, host: str) -> str:
    pattern = re.compile(
        r"(?ms)^# >>> agent-bridge:{0} >>>\r?\n.*?^# <<< agent-bridge:{0} <<<\r?\n?".format(re.escape(host))
    )
    return pattern.sub("", source)


def _append_block(source: str, block: str) -> str:
    return source + ("" if not source or source.endswith("\n") else "\n") + block
