"""Typed contracts and safe host integration configuration helpers."""

from __future__ import annotations

import abc
import ctypes
import enum
import json
import os
import re
import secrets
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

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

    @property
    def installation_artifact_path(self) -> Path:
        """Bridge-owned receipt proving this exact consumer was installed."""
        return self.home / ".agent-bridge" / "host-integrations" / (self.name + ".json")

    def task_card_path(self, task_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", task_id):
            raise ValueError("task_id must be a safe bounded identifier")
        path = self.inbox_path / (task_id + ".json")
        self._assert_contained(path)
        return path

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
        found = self._valid_installation_marker() and self._consumer_is_installed() and self._installation_artifact_is_valid()
        detail = "validated host marker, managed consumer, and installation artifact found" if found else "host marker, exact managed consumer, or installation artifact is absent or incompatible"
        return HostDetection(self.name, found, self.config_path, detail)

    def plan_install(self) -> InstallPlan:
        if not self._valid_installation_marker():
            return InstallPlan(self.name, self.config_path, (), "host is not detected; no configuration will be written")
        return InstallPlan(self.name, self.config_path, ("install managed session-card consumer",))

    def install(self, plan: Optional[InstallPlan] = None) -> OperationResult:
        actual_plan = plan or self.plan_install()
        if actual_plan.host != self.name or actual_plan.config_path != self.config_path:
            raise ValueError("install plan does not belong to this host")
        if not self._valid_installation_marker():
            return OperationResult(self.name, False, DeliveryStatus.FAILED, "host is not detected; integration was not installed")
        self._assert_contained(self.config_path)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with _config_lock(self.config_path):
            config_installed = False
            try:
                self._install_config()
                config_installed = True
                self._write_installation_artifact()
            except BaseException:
                if config_installed:
                    try:
                        self._uninstall_config()
                    finally:
                        self._remove_installation_artifact()
                raise
        return OperationResult(self.name, True, DeliveryStatus.QUEUED, "managed session-card consumer installed")

    def uninstall(self) -> OperationResult:
        try:
            self._assert_contained(self.config_path)
        except ValueError:
            return OperationResult(self.name, False, DeliveryStatus.FAILED, "host configuration escapes its home")
        if not self.config_path.exists():
            return OperationResult(self.name, True, DeliveryStatus.QUEUED, "no managed integration was installed")
        with _config_lock(self.config_path):
            self._uninstall_config()
            self._remove_installation_artifact()
        self._cleanup_pending_cards()
        return OperationResult(self.name, True, DeliveryStatus.QUEUED, "managed integration removed")

    def health_check(self) -> HealthCheck:
        if self.detect().found:
            return HealthCheck(self.name, True, self.capabilities())
        capabilities = self.capabilities()
        return HealthCheck(
            self.name,
            False,
            HostCapabilities(Surface.TERMINAL_FALLBACK, False, False, False, capabilities.protocol_version, capabilities.integration_version),
            "host integration is unavailable; use the terminal fallback",
        )

    def notify_in_app(self, task: TaskCard, service) -> OperationResult:
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
        try:
            self.inbox_path.mkdir(parents=True, exist_ok=True)
            with _config_lock(self.task_card_path(task.task_id)):
                self._write_card_temporary(task.task_id, payload)
                service.register_host_delivery_proof(
                    task.task_id, self.name, capabilities.integration_version, capabilities.protocol_version, token,
                )
                self._publish_temporary_card(task.task_id)
        except (OSError, ValueError) as error:
            try:
                service.cancel_host_delivery_proof(task.task_id, self.name, token)
            except Exception:
                pass
            return OperationResult(self.name, False, DeliveryStatus.FAILED, "unable to queue a contained task card: {0}".format(error))
        return OperationResult(self.name, True, DeliveryStatus.QUEUED, "session card queued for host consumer")

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
            self._assert_contained(self.marker_path)
            if self.marker_path.is_symlink() or not self.marker_path.is_file():
                return False
            marker = json.loads(self.marker_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return isinstance(marker, dict) and marker.get("host") == self.name and self.mechanism in marker.get("mechanisms", ())

    def _managed_config_text(self) -> Optional[str]:
        try:
            self._assert_contained(self.config_path)
            if self.config_path.is_symlink() or not self.config_path.is_file():
                return None
            return self.config_path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            return None

    def _installation_artifact_is_valid(self) -> bool:
        try:
            self._assert_contained(self.installation_artifact_path)
            if self.installation_artifact_path.is_symlink() or not self.installation_artifact_path.is_file():
                return False
            artifact = json.loads(self.installation_artifact_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return artifact == {"host_identity": self.name, "entrypoint": self._entrypoint()}

    def _owned_entrypoint(self) -> Optional[list]:
        """Read the immutable install receipt for removal across upgrades."""
        try:
            artifact = json.loads(self.installation_artifact_path.read_text(encoding="utf-8"))
            entrypoint = artifact.get("entrypoint") if isinstance(artifact, dict) else None
            if artifact.get("host_identity") != self.name or not isinstance(entrypoint, list) or not entrypoint or not all(isinstance(item, str) for item in entrypoint):
                return None
            return entrypoint
        except (OSError, ValueError, AttributeError):
            return None

    def _write_installation_artifact(self) -> None:
        self._assert_contained(self.installation_artifact_path)
        self.installation_artifact_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(
            self.installation_artifact_path,
            json.dumps({"host_identity": self.name, "entrypoint": self._entrypoint()}, ensure_ascii=False, sort_keys=True) + "\n",
        )

    def _remove_installation_artifact(self) -> None:
        try:
            self._assert_contained(self.installation_artifact_path)
            if self.installation_artifact_path.is_file() and not self.installation_artifact_path.is_symlink():
                self.installation_artifact_path.unlink()
        except OSError:
            pass

    def _assert_contained(self, path: Path) -> None:
        """Reject symlinks and traversal before touching host-owned files."""
        root = self.home.resolve(strict=False)
        candidate = Path(path)
        try:
            candidate.resolve(strict=False).relative_to(root)
            relative = candidate.absolute().relative_to(self.home.absolute())
        except ValueError as error:
            raise ValueError("host path escapes its home") from error
        cursor = self.home
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ValueError("host path contains a symlink")

    def _card_temporary_path(self, task_id: str) -> Path:
        return self.task_card_path(task_id).with_name(task_id + ".pending.json")

    def _write_card_temporary(self, task_id: str, payload: dict) -> None:
        destination = self.task_card_path(task_id)
        self._assert_contained(destination)
        self.inbox_path.mkdir(parents=True, exist_ok=True)
        temporary = self._card_temporary_path(task_id)
        self._assert_contained(temporary)
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                stream.write("\n")
        finally:
            pass

    def _publish_temporary_card(self, task_id: str) -> None:
        temporary = self._card_temporary_path(task_id)
        destination = self.task_card_path(task_id)
        os.replace(temporary, destination)

    def _read_card(self, task_id: str) -> Optional[dict]:
        try:
            card = json.loads(self.task_card_path(task_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return card if isinstance(card, dict) else None

    def _cleanup_pending_cards(self) -> None:
        """Remove only regular, safe card files owned by this host inbox."""
        try:
            self._assert_contained(self.inbox_path)
        except ValueError:
            return
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
        _optimistic_update(self.config_path, lambda source: _append_block(_remove_toml_block(source, self.name), self._managed_block()))

    def _uninstall_config(self) -> None:
        _optimistic_update(self.config_path, lambda source: _remove_toml_block(source, self.name))

    def _consumer_is_installed(self) -> bool:
        text = self._managed_config_text()
        if text is None:
            return False
        return self._managed_block() in text

    def _managed_block(self) -> str:
        capabilities = self.capabilities()
        lines = (
            "[{0}]".format(self.managed_table),
            "host_identity = {0}".format(json.dumps(self.name)),
            "integration_version = {0}".format(json.dumps(capabilities.integration_version)),
            "protocol_version = {0}".format(capabilities.protocol_version),
            "surface = \"session_card\"",
            "inbox = {0}".format(json.dumps(str(self.inbox_path))),
            "command = {0}".format(json.dumps(sys.executable)),
            "args = {0}".format(json.dumps(self._entrypoint()[1:])),
        )
        return "# >>> agent-bridge:{0} >>>\n{1}\n# <<< agent-bridge:{0} <<<\n".format(self.name, "\n".join(lines))

    def _entrypoint(self) -> list:
        return [
            sys.executable, "-m", "agent_bridge.adapters.integration", "serve", "--host", self.name,
            "--home", str(self.home), "--data-root", str(self.home / ".agent-bridge"),
        ]


class ManagedJsonAdapter(HostAdapter):
    """Structured JSON ownership that merges removal with concurrent user edits."""

    def _managed_config(self, root: dict) -> None:
        """Host-specific documented configuration mutation."""

    def _install_config(self) -> None:
        def update(root: dict) -> None:
            self._managed_config(root)
            root["agent_bridge"] = self._managed_metadata()
        _optimistic_json_update(self.config_path, update)

    def _uninstall_config(self) -> None:
        def update(root: dict) -> None:
            if root.get("agent_bridge") == self._managed_metadata():
                root.pop("agent_bridge", None)
            self._remove_legacy_managed(root)
        _optimistic_json_update(self.config_path, update)

    def _consumer_is_installed(self) -> bool:
        try:
            if self._managed_config_text() is None:
                return False
            managed = _read_json_object(self.config_path).get("agent_bridge")
        except ValueError:
            return False
        return managed == self._managed_metadata()

    def _managed_metadata(self) -> dict:
        capabilities = self.capabilities()
        return {
            "host_identity": self.name,
            "integration_version": capabilities.integration_version,
            "protocol_version": capabilities.protocol_version,
            "surface": Surface.SESSION_CARD.value,
            "inbox": str(self.inbox_path),
            "command": sys.executable,
            "args": self._entrypoint()[1:],
        }

    def _entrypoint(self) -> list:
        return [
            sys.executable, "-m", "agent_bridge.adapters.integration", "serve", "--host", self.name,
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


def _optimistic_json_update(path: Path, update) -> None:
    def transform(source: str) -> str:
        try:
            root = json.loads(source or "{}")
        except json.JSONDecodeError as error:
            raise ValueError("cannot update invalid JSON host config") from error
        if not isinstance(root, dict):
            raise ValueError("host config must contain a JSON object")
        update(root)
        return json.dumps(root, ensure_ascii=False, indent=2) + "\n"
    _optimistic_update(path, transform)


def _optimistic_update(path: Path, transform) -> None:
    """Apply a config mutation without discarding a concurrent external edit."""
    source = path.read_text(encoding="utf-8") if path.exists() else ""
    expected_on_disk = source
    for ignored in range(4):
        replacement = transform(source)
        result = _atomic_write(path, replacement, expected_on_disk)
        if result.matched:
            return
        source = result.displaced_source
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        expected_on_disk = replacement if current == replacement else source
    raise RuntimeError("host config changed repeatedly; retry the operation")


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


class _ExternalConfigChanged(RuntimeError):
    pass


@dataclass(frozen=True)
class _AtomicWriteResult:
    matched: bool
    displaced_source: str = ""


def _before_config_swap(path: Path) -> None:
    """Test seam for an uncooperative writer in the compare-to-swap window."""


def _atomic_write(path: Path, text: str, expected_source: Optional[str] = None) -> _AtomicWriteResult:
    if expected_source is not None:
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if current != expected_source:
            return _AtomicWriteResult(False, current)
    descriptor, temporary_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if expected_source is None:
            os.replace(str(temporary), str(path))
            return _AtomicWriteResult(True)
        _before_config_swap(path)
        if not path.exists():
            try:
                os.link(str(temporary), str(path))
            except (FileExistsError, OSError):
                return _AtomicWriteResult(False, path.read_text(encoding="utf-8") if path.exists() else "")
            return _AtomicWriteResult(True)
        if os.name != "nt":
            raise RuntimeError("safe atomic config compare-and-swap is unsupported on this platform")
        backup = Path(tempfile.mktemp(prefix=path.name + ".", suffix=".bak", dir=str(path.parent)))
        replaced = ctypes.windll.kernel32.ReplaceFileW(str(path), str(temporary), str(backup), 0, None, None)
        if not replaced:
            raise OSError(ctypes.get_last_error(), "ReplaceFileW failed")
        displaced = backup.read_text(encoding="utf-8") if backup.exists() else ""
        try:
            backup.unlink()
        except OSError:
            pass
        return _AtomicWriteResult(displaced == expected_source, displaced)
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
