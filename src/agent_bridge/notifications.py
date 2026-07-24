"""Bounded, shell-free client for optional native notification helpers."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .models import DeliveryStatus
from .outbox import utc_now
from .store import Store


MAX_JSON_BYTES = 16 * 1024
DEFAULT_MAX_OUTPUT_BYTES = 16 * 1024
DEFAULT_TIMEOUT_SECONDS = 2.0
_ACTIONS = ("view", "claim", "snooze")


@dataclass(frozen=True)
class NotificationNotice:
    """The small, display-safe portion of a durable notification intent."""

    title: str
    body: str
    task_id: str
    expires_in_seconds: int = 30


@dataclass(frozen=True)
class NotificationResult:
    """Only the evidence a native helper explicitly returned."""

    ok: bool
    status: DeliveryStatus
    detail: str
    notification_id: str = ""


@dataclass(frozen=True)
class NotificationCapability:
    """A doctor-facing statement of native delivery availability, never a delivery claim."""

    available: bool
    helper_path: str
    detail: str


class WindowsNotifier:
    """Invoke the installed Windows toast helper with one bounded JSON message."""

    def __init__(
        self,
        helper_path: Path | str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> None:
        self.helper_path = Path(helper_path)
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes

    def post(self, notice: NotificationNotice) -> NotificationResult:
        return self._request("post", notice)

    def register(self, activation_argv: Sequence[str]) -> NotificationResult:
        return self._request("register", activation_argv=list(activation_argv))

    def unregister(self) -> NotificationResult:
        return self._request("unregister")

    def status(self) -> NotificationResult:
        return self._request("status")

    def action(self, action: str, notification_id: str, task_id: str) -> NotificationResult:
        if action not in _ACTIONS:
            return _failure("invalid action")
        return self._request("action", action=action, notification_id=notification_id, task_id=task_id)

    def _request(self, operation: str, notice: NotificationNotice | None = None, **extra: str) -> NotificationResult:
        try:
            payload = _request_payload(operation, notice, extra)
        except ValueError as error:
            return _failure(str(error))
        try:
            returncode, stdout, stderr, reason = _run_capped_helper(
                self.helper_path, payload.encode("utf-8"), self.timeout_seconds, self.max_output_bytes
            )
        except (OSError, UnicodeError) as error:
            return _failure("native notification helper unavailable: {0}".format(error))
        if reason:
            return _failure(reason)
        try:
            stdout_text = stdout.decode("utf-8", errors="strict")
            stderr_text = stderr.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return _failure("native notification helper returned invalid UTF-8")
        if returncode != 0:
            return _failure(_bounded_detail(stderr_text) or "native notification helper exited {0}".format(returncode))
        return _parse_response(stdout_text)


def _run_capped_helper(path: Path, payload: bytes, timeout: float, limit: int) -> tuple[int, bytes, bytes, str]:
    """Drain both pipes concurrently; overflow and timeout always kill and join."""
    process = subprocess.Popen([str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
    streams = [bytearray(), bytearray()]
    overflow = threading.Event()
    def drain(index: int, pipe: Any) -> None:
        while True:
            block = pipe.read(4096)
            if not block:
                return
            streams[index].extend(block)
            if len(streams[index]) > limit:
                overflow.set()
                return
    readers = [threading.Thread(target=drain, args=(0, process.stdout), daemon=True), threading.Thread(target=drain, args=(1, process.stderr), daemon=True)]
    for reader in readers: reader.start()
    assert process.stdin is not None
    try:
        process.stdin.write(payload); process.stdin.close()
        deadline = time.monotonic() + timeout
        while process.poll() is None and not overflow.is_set() and time.monotonic() < deadline:
            time.sleep(0.005)
        if process.poll() is None:
            process.kill()
            process.wait()
            reason = "native notification helper output too large" if overflow.is_set() else "native notification helper timed out"
        else:
            reason = ""
    finally:
        if process.poll() is None:
            process.kill(); process.wait()
        for reader in readers: reader.join(1.0)
        for pipe in (process.stdout, process.stderr):
            if pipe is not None: pipe.close()
        if overflow.is_set():
            reason = "native notification helper output too large"
    return process.returncode or 0, bytes(streams[0]), bytes(streams[1]), reason


def _request_payload(operation: str, notice: NotificationNotice | None, extra: Mapping[str, str]) -> str:
    if operation not in ("post", "register", "unregister", "status", "action"):
        raise ValueError("invalid native notification operation")
    payload: dict[str, Any] = {"operation": operation}
    if operation == "register":
        argv = extra.get("activation_argv")
        if not isinstance(argv, list) or not argv or len(argv) > 16 or any(not isinstance(part, str) or not part or len(part) > 1024 for part in argv) or not Path(argv[0]).is_absolute():
            raise ValueError("invalid activation_argv")
        payload["activation_argv"] = argv
    elif operation == "post":
        if notice is None:
            raise ValueError("post requires a notification")
        payload.update({
            "title": _bounded_text(notice.title, "title", 256),
            "body": _bounded_text(notice.body, "body", 2048),
            "task_id": _bounded_opaque_id(notice.task_id, "task_id"),
            "actions": list(_ACTIONS),
            "expires_in_seconds": _bounded_expiry(notice.expires_in_seconds),
        })
    elif operation == "action":
        payload.update({
            "action": _bounded_action(extra.get("action", "")),
            "notification_id": _bounded_opaque_id(extra.get("notification_id", ""), "notification_id"),
            "task_id": _bounded_opaque_id(extra.get("task_id", ""), "task_id"),
        })
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_JSON_BYTES:
        raise ValueError("native notification request too large")
    return encoded


def _parse_response(stdout: str) -> NotificationResult:
    try:
        response = json.loads(stdout)
    except (TypeError, ValueError):
        return _failure("native notification helper returned malformed JSON")
    required = {"ok", "notification_id", "status", "detail"}
    if not isinstance(response, dict) or not set(response).issubset(required):
        return _failure("native notification helper returned invalid response fields")
    if "notification_id" not in response:
        return _failure("native notification helper response requires notification_id")
    if set(response) != required:
        return _failure("native notification helper returned incomplete response")
    if response.get("ok") is not True:
        return _failure(_bounded_detail(response.get("detail")) or "native notification helper failed")
    notification_id = response.get("notification_id")
    if not isinstance(notification_id, str) or not notification_id or len(notification_id) > 256:
        return _failure("native notification helper response requires notification_id")
    if response.get("status") != DeliveryStatus.OS_POSTED.value:
        return _failure("native notification helper returned invalid success status")
    detail = _bounded_detail(response.get("detail"))
    if detail is None:
        return _failure("native notification helper returned invalid detail")
    return NotificationResult(True, DeliveryStatus.OS_POSTED, detail, notification_id)


def _bounded_text(value: object, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError("invalid {0}".format(name))
    return value


def _bounded_opaque_id(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256 or any(not (character.isascii() and (character.isalnum() or character in "._-")) for character in value):
        raise ValueError("invalid {0}".format(name))
    return value


def _bounded_expiry(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 86_400:
        raise ValueError("invalid expires_in_seconds")
    return value


def _bounded_action(value: object) -> str:
    if value not in _ACTIONS:
        raise ValueError("invalid action")
    return str(value)


def _bounded_detail(value: object) -> str | None:
    return value if isinstance(value, str) and len(value) <= 1024 else None


def _failure(detail: str) -> NotificationResult:
    return NotificationResult(False, DeliveryStatus.FAILED, detail)


def windows_notification_capability() -> NotificationCapability:
    """Report the optional packaged helper honestly without probing or registering it."""
    configured = os.environ.get("AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER", "").strip()
    if not configured:
        return NotificationCapability(False, "", "native Windows toast helper is not installed")
    helper = Path(configured)
    if not helper.is_file():
        return NotificationCapability(False, str(helper), "configured native Windows toast helper is not installed")
    if os.name != "nt":
        return NotificationCapability(False, str(helper), "native Windows toast helper is unavailable on this platform")
    result = WindowsNotifier(helper, timeout_seconds=1.0).status()
    return NotificationCapability(result.ok, str(helper), result.detail)


class WindowsNotificationChannel:
    """Dispatcher adapter that records a native ID only after WinRT accepts it."""

    def __init__(self, database_path: Path | str, helper_path: Path | str) -> None:
        self.database_path = str(database_path)
        self.helper_path = Path(helper_path)

    def applicable(self, item: Any) -> bool:
        del item
        return os.name == "nt" and self.helper_path.is_file()

    def deliver(self, item: Any, idempotency_key: str, timeout_seconds: float) -> DeliveryStatus:
        del idempotency_key
        task_id = item.payload.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise RuntimeError("notification item requires task_id")
        store = Store.open(Path(self.database_path))
        try:
            task = store.connection.execute(
                "SELECT subject, body FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if task is None:
                raise RuntimeError("notification task no longer exists")
            result = WindowsNotifier(self.helper_path, timeout_seconds=timeout_seconds).post(
                NotificationNotice(str(task["subject"]), str(task["body"]), task_id)
            )
            if not result.ok or result.status is not DeliveryStatus.OS_POSTED:
                raise RuntimeError(result.detail)
            with store.transaction(immediate=True) as connection:
                # The native ID is the primary key, making repeats idempotent while retaining the opaque task mapping.
                connection.execute(
                    "INSERT INTO notification_mappings(notification_id, task_id, action, created_at) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(notification_id) DO UPDATE SET "
                    "task_id = excluded.task_id, action = excluded.action, created_at = excluded.created_at",
                    (result.notification_id, task_id, "view", utc_now()),
                )
            return DeliveryStatus.OS_POSTED
        finally:
            store.close()

    def resolve_action(self, notification_id: str, action: str) -> tuple[str, str]:
        """Resolve only a fixed action and a locally stored opaque notification ID."""
        if action not in _ACTIONS:
            raise ValueError("invalid action")
        _bounded_opaque_id(notification_id, "notification_id")
        store = Store.open(Path(self.database_path))
        try:
            row = store.connection.execute(
                "SELECT task_id FROM notification_mappings WHERE notification_id = ?", (notification_id,)
            ).fetchone()
        finally:
            store.close()
        if row is None:
            raise KeyError("unknown native notification")
        return str(row["task_id"]), action
