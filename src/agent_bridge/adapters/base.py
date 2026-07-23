"""Typed contracts and safe host integration configuration helpers."""

from __future__ import annotations

import abc
import enum
import json
import os
import re
import secrets
import tempfile
import time
from contextlib import contextmanager
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
        if not isinstance(self.surface, Surface):
            raise TypeError("surface must be a Surface")
        for name in ("can_ack", "can_open_terminal", "can_receive_context"):
            if type(getattr(self, name)) is not bool:
                raise TypeError("{0} must be a bool".format(name))
        if type(self.protocol_version) is not int:
            raise TypeError("protocol_version must be an int")
        if self.protocol_version < 1:
            raise ValueError("protocol_version must be an integer greater than zero")
        if not isinstance(self.integration_version, str):
            raise TypeError("integration_version must be a string")
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
    acknowledged: bool = False


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
        _bounded_text("task_id", self.task_id, 128, required=True)
        _bounded_text("subject", self.subject, 256, required=True)
        _bounded_text("body", self.body, 8192)


@dataclass(frozen=True)
class TaskAcknowledgement:
    """An integration-side ACK; it is validated against the queued card."""

    host_identity: str
    task_id: str
    integration_version: str
    protocol_version: int
    delivery_token: str

    def __post_init__(self) -> None:
        _bounded_text("host_identity", self.host_identity, 64, required=True)
        _bounded_text("task_id", self.task_id, 128, required=True)
        _bounded_text("integration_version", self.integration_version, 32, required=True)
        _bounded_text("delivery_token", self.delivery_token, 128, required=True)
        if type(self.protocol_version) is not int or self.protocol_version < 1:
            raise ValueError("protocol_version must be a positive int")

    def as_shared_payload(self) -> dict:
        return {
            "host_identity": self.host_identity,
            "task_id": self.task_id,
            "integration_version": self.integration_version,
            "protocol_version": self.protocol_version,
        }


@dataclass(frozen=True)
class HostIdentity:
    name: str
    aliases: Tuple[str, ...] = ()


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


SharedAcknowledge = Callable[..., None]


class HostAdapter(abc.ABC):
    """Strict host contract with an explicit, consumer-side ACK boundary."""

    name: str
    fixture_suffix: str
    mechanism: str
    relative_config_path: Tuple[str, ...]
    relative_marker_path: Tuple[str, ...]

    def __init__(self, home: Path) -> None:
        self.home = Path(home)

    @property
    def config_path(self) -> Path:
        return self.home.joinpath(*self.relative_config_path)

    @property
    def marker_path(self) -> Path:
        return self.home.joinpath(*self.relative_marker_path)

    @property
    def inbox_path(self) -> Path:
        return self.home / ".agent-bridge" / "session-cards" / self.name

    def task_card_path(self, task_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", task_id):
            raise ValueError("task_id must be a safe bounded identifier")
        return self.inbox_path / (task_id + ".json")

    @abc.abstractmethod
    def capabilities(self) -> HostCapabilities:
        """Return actual capabilities, never aspirational ones."""

    @abc.abstractmethod
    def _install_config(self) -> None:
        """Apply only this integration's managed configuration."""

    @abc.abstractmethod
    def _uninstall_config(self) -> None:
        """Remove only this integration's managed configuration."""

    @abc.abstractmethod
    def _consumer_is_installed(self) -> bool:
        """Whether the host has this integration's actual consumer entrypoint."""

    def detect(self) -> HostDetection:
        found = self._valid_installation_marker()
        detail = "validated host installation marker found" if found else "host installation marker is absent or incompatible"
        return HostDetection(self.name, found, self.config_path, detail)

    def plan_install(self) -> InstallPlan:
        if not self.detect().found:
            return InstallPlan(self.name, self.config_path, (), "host is not detected; no configuration will be written")
        return InstallPlan(self.name, self.config_path, ("install managed session-card consumer",))

    def install(self, plan: Optional[InstallPlan] = None) -> OperationResult:
        actual_plan = plan or self.plan_install()
        if actual_plan.host != self.name or actual_plan.config_path != self.config_path:
            raise ValueError("install plan does not belong to this host")
        if not self.detect().found:
            return OperationResult(self.name, False, DeliveryStatus.FAILED, "host is not detected; integration was not installed")
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with _config_lock(self.config_path):
            self._install_config()
        return OperationResult(self.name, True, DeliveryStatus.QUEUED, "managed session-card consumer installed")

    def uninstall(self) -> OperationResult:
        if not self.config_path.exists():
            return OperationResult(self.name, True, DeliveryStatus.QUEUED, "no managed integration was installed")
        with _config_lock(self.config_path):
            self._uninstall_config()
        self._cleanup_pending_cards()
        return OperationResult(self.name, True, DeliveryStatus.QUEUED, "managed integration removed")

    def health_check(self) -> HealthCheck:
        if self.detect().found and self._consumer_is_installed():
            return HealthCheck(self.name, True, self.capabilities())
        capabilities = self.capabilities()
        return HealthCheck(
            self.name,
            False,
            HostCapabilities(Surface.TERMINAL_FALLBACK, False, False, False, capabilities.protocol_version, capabilities.integration_version),
            "host integration is unavailable; use the terminal fallback",
        )

    def notify_in_app(self, task: TaskCard) -> OperationResult:
        if not isinstance(task, TaskCard):
            raise TypeError("task must be a TaskCard")
        if not self.detect().found:
            return OperationResult(self.name, False, DeliveryStatus.FAILED, "host is not detected; task was not delivered")
        capabilities = self.capabilities()
        if capabilities.surface == Surface.TERMINAL_FALLBACK or not capabilities.can_receive_context:
            return OperationResult(self.name, False, DeliveryStatus.FAILED, "host has no in-application task-card surface")
        if not self._consumer_is_installed():
            return OperationResult(self.name, False, DeliveryStatus.FAILED, "host session-card consumer is not installed")
        token = secrets.token_urlsafe(24)
        payload = {
            "host_identity": self.name,
            "task_id": task.task_id,
            "subject": task.subject,
            "body": task.body,
            "integration_version": capabilities.integration_version,
            "protocol_version": capabilities.protocol_version,
            "delivery_token": token,
        }
        self._write_card_atomically(task.task_id, payload)
        return OperationResult(self.name, True, DeliveryStatus.QUEUED, "session card queued for host consumer")

    def acknowledge_integration(
        self, acknowledgement: TaskAcknowledgement, shared_acknowledge: SharedAcknowledge
    ) -> OperationResult:
        """Accept only an explicit host consumer ACK matching a queued task card."""
        if not isinstance(acknowledgement, TaskAcknowledgement):
            raise TypeError("acknowledgement must be a TaskAcknowledgement")
        if not callable(shared_acknowledge):
            raise TypeError("shared_acknowledge must be callable")
        if not self.detect().found or not self._consumer_is_installed():
            return OperationResult(self.name, False, DeliveryStatus.FAILED, "host session-card consumer is unavailable")
        capabilities = self.capabilities()
        if (
            acknowledgement.host_identity != self.name
            or acknowledgement.integration_version != capabilities.integration_version
            or acknowledgement.protocol_version != capabilities.protocol_version
        ):
            return OperationResult(self.name, False, DeliveryStatus.FAILED, "acknowledgement does not match host capabilities")
        card = self._read_card(acknowledgement.task_id)
        expected = acknowledgement.as_shared_payload()
        if card is None or any(card.get(key) != value for key, value in expected.items()):
            return OperationResult(self.name, False, DeliveryStatus.FAILED, "acknowledgement does not match a queued task card")
        if card.get("delivery_token") != acknowledgement.delivery_token:
            return OperationResult(self.name, False, DeliveryStatus.FAILED, "acknowledgement delivery token is invalid")
        try:
            shared_acknowledge(**expected)
        except Exception as error:
            return OperationResult(self.name, False, DeliveryStatus.FAILED, "shared acknowledgement failed: {0}".format(error))
        return OperationResult(self.name, True, DeliveryStatus.AGENT_ACKNOWLEDGED, "host consumer acknowledged queued task card", True)

    def integration_acknowledgement(self, task_id: str) -> TaskAcknowledgement:
        """Read the host-consumed card and prepare its explicit ACK request."""
        if not self.detect().found or not self._consumer_is_installed():
            raise ValueError("host session-card consumer is unavailable")
        card = self._read_card(task_id)
        if card is None:
            raise ValueError("queued task card is unavailable")
        try:
            acknowledgement = TaskAcknowledgement(
                str(card["host_identity"]),
                str(card["task_id"]),
                str(card["integration_version"]),
                card["protocol_version"],
                str(card["delivery_token"]),
            )
        except (KeyError, ValueError) as error:
            raise ValueError("queued task card is invalid") from error
        if acknowledgement.host_identity != self.name or acknowledgement.task_id != task_id:
            raise ValueError("queued task card does not belong to this host")
        capabilities = self.capabilities()
        if (
            acknowledgement.integration_version != capabilities.integration_version
            or acknowledgement.protocol_version != capabilities.protocol_version
        ):
            raise ValueError("queued task card does not match host capabilities")
        return acknowledgement

    def consume_acknowledged_card(self, task_id: str, delivery_token: str) -> None:
        """Atomically consume the exact card once its durable ACK commits."""
        path = self.task_card_path(task_id)
        card = self._read_card(task_id)
        if card is None or card.get("delivery_token") != delivery_token:
            raise ValueError("queued task card is unavailable")
        consumed = path.with_name(path.name + ".consumed-" + secrets.token_hex(8))
        try:
            os.replace(path, consumed)
        except OSError as error:
            raise ValueError("queued task card was already consumed") from error
        try:
            consumed.unlink()
        except OSError:
            pass

    def launch(self, task: TaskCard) -> OperationResult:
        if not self.detect().found:
            return OperationResult(self.name, False, DeliveryStatus.FAILED, "host is not detected; launch was not attempted")
        return OperationResult(self.name, False, DeliveryStatus.FAILED, "host integration does not expose process launch")

    def open_terminal(self, task: TaskCard) -> OperationResult:
        if not self.detect().found:
            return OperationResult(self.name, False, DeliveryStatus.FAILED, "host is not detected; terminal was not opened")
        return OperationResult(self.name, False, DeliveryStatus.FAILED, "use the platform terminal fallback")

    def _valid_installation_marker(self) -> bool:
        try:
            marker = json.loads(self.marker_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return isinstance(marker, dict) and marker.get("host") == self.name and self.mechanism in marker.get("mechanisms", ())

    def _write_card_atomically(self, task_id: str, payload: dict) -> None:
        self.inbox_path.mkdir(parents=True, exist_ok=True)
        destination = self.task_card_path(task_id)
        temporary = destination.with_name(destination.name + "." + secrets.token_hex(8) + ".tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                stream.write("\n")
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _read_card(self, task_id: str) -> Optional[dict]:
        try:
            card = json.loads(self.task_card_path(task_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return card if isinstance(card, dict) else None

    def _cleanup_pending_cards(self) -> None:
        """Remove only regular, safe card files owned by this host inbox."""
        if not self.inbox_path.is_dir() or self.inbox_path.is_symlink():
            return
        for path in self.inbox_path.glob("*.json"):
            if path.is_symlink():
                continue
            try:
                self.task_card_path(path.stem)
                path.unlink()
            except (OSError, ValueError):
                continue


class ManagedTomlAdapter(HostAdapter):
    """Named managed TOML table support that leaves user-owned text unchanged."""

    managed_table = "agent_bridge"

    def _install_config(self) -> None:
        source = self.config_path.read_text(encoding="utf-8") if self.config_path.exists() else ""
        cleaned = _remove_toml_block(source, self.name)
        capabilities = self.capabilities()
        lines = (
            "[{0}]".format(self.managed_table),
            "host_identity = {0}".format(json.dumps(self.name)),
            "integration_version = {0}".format(json.dumps(capabilities.integration_version)),
            "protocol_version = {0}".format(capabilities.protocol_version),
            "surface = \"session_card\"",
            "inbox = {0}".format(json.dumps(str(self.inbox_path))),
            "command = \"python\"",
            "args = {0}".format(json.dumps(self._entrypoint()[1:])),
        )
        block = "# >>> agent-bridge:{0} >>>\n{1}\n# <<< agent-bridge:{0} <<<\n".format(self.name, "\n".join(lines))
        _atomic_write(self.config_path, _append_block(cleaned, block))

    def _uninstall_config(self) -> None:
        _atomic_write(self.config_path, _remove_toml_block(self.config_path.read_text(encoding="utf-8"), self.name))

    def _consumer_is_installed(self) -> bool:
        try:
            text = self.config_path.read_text(encoding="utf-8")
        except OSError:
            return False
        return "# >>> agent-bridge:{0} >>>".format(self.name) in text and "command = \"python\"" in text and "serve" in text

    def _entrypoint(self) -> list:
        return [
            "python", "-m", "agent_bridge.adapters.integration", "serve", "--host", self.name,
            "--home", str(self.home), "--data-root", str(self.home / ".agent-bridge"),
        ]


class ManagedJsonAdapter(HostAdapter):
    """Structured JSON ownership that merges removal with concurrent user edits."""

    def _managed_config(self, root: dict) -> None:
        """Host-specific documented configuration mutation."""

    def _install_config(self) -> None:
        root = _read_json_object(self.config_path)
        self._managed_config(root)
        capabilities = self.capabilities()
        root["agent_bridge"] = {
            "host_identity": self.name,
            "integration_version": capabilities.integration_version,
            "protocol_version": capabilities.protocol_version,
            "surface": Surface.SESSION_CARD.value,
            "inbox": str(self.inbox_path),
            "command": "python",
            "args": self._entrypoint()[1:],
        }
        _write_json_object(self.config_path, root)

    def _uninstall_config(self) -> None:
        root = _read_json_object(self.config_path)
        root.pop("agent_bridge", None)
        self._remove_legacy_managed(root)
        _write_json_object(self.config_path, root)

    def _consumer_is_installed(self) -> bool:
        try:
            managed = _read_json_object(self.config_path).get("agent_bridge")
        except ValueError:
            return False
        return isinstance(managed, dict) and managed.get("host_identity") == self.name and managed.get("command") == "python" and managed.get("args") == self._entrypoint()[1:]

    def _entrypoint(self) -> list:
        return [
            "python", "-m", "agent_bridge.adapters.integration", "serve", "--host", self.name,
            "--home", str(self.home), "--data-root", str(self.home / ".agent-bridge"),
        ]

    def _remove_legacy_managed(self, root: dict) -> None:
        """Remove pre-v2 data owned by a host integration."""


def _read_json_object(path: Path) -> dict:
    source = path.read_text(encoding="utf-8") if path.exists() else "{}\n"
    try:
        root = json.loads(source)
    except json.JSONDecodeError as error:
        raise ValueError("cannot update invalid JSON host config") from error
    if not isinstance(root, dict):
        raise ValueError("host config must contain a JSON object")
    return root


def _write_json_object(path: Path, root: dict) -> None:
    _atomic_write(path, json.dumps(root, ensure_ascii=False, indent=2) + "\n")


@contextmanager
def _config_lock(path: Path):
    lock = path.with_name(path.name + ".agent-bridge.lock")
    deadline = time.monotonic() + 5.0
    descriptor = None
    while descriptor is None:
        try:
            descriptor = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, str(os.getpid()).encode("ascii"))
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise RuntimeError("timed out waiting for host config lock")
            time.sleep(0.02)
    try:
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            lock.unlink()
        except OSError:
            pass


def _atomic_write(path: Path, text: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(path))
        try:
            parent_descriptor = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(parent_descriptor)
            finally:
                os.close(parent_descriptor)
        except OSError:
            pass
    finally:
        if temporary.exists():
            temporary.unlink()


def _bounded_text(name: str, value: object, maximum: int, required: bool = False) -> None:
    if not isinstance(value, str) or (required and not value) or len(value) > maximum or "\x00" in value:
        raise ValueError("{0} must be a {1}bounded string".format(name, "non-empty " if required else ""))


def _remove_toml_block(source: str, host: str) -> str:
    pattern = re.compile(r"(?ms)^# >>> agent-bridge:{0} >>>\r?\n.*?^# <<< agent-bridge:{0} <<<\r?\n?".format(re.escape(host)))
    return pattern.sub("", source)


def _append_block(source: str, block: str) -> str:
    return source + ("" if not source or source.endswith("\n") else "\n") + block
