"""Local policy checks and argv-safe agent process launching."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Sequence, Tuple, Union

from .delivery import DeferredDelivery
from .models import AgentProfile, DeliveryStatus, ExecutionPolicy
from .outbox import utc_now
from .store import Store


_SHELL_METACHARACTERS = frozenset("|&;<>()$`\r\n%^!")
RESERVATION_SECONDS = 300


class LaunchPolicyError(ValueError):
    """A locally stored launch profile cannot be used safely."""


@dataclass(frozen=True)
class LaunchDecision:
    """The complete, content-free command authorized by local policy."""

    allowed: bool
    reason: str
    argv: Tuple[str, ...] = ()
    workspace: str = ""


@dataclass(frozen=True)
class LaunchResult:
    """Evidence that a configured process was started (not that it acted)."""

    started: bool
    reason: str = ""
    pid: Optional[int] = None
    pending_until: Optional[str] = None


@dataclass(frozen=True)
class _Reservation:
    existing: bool
    decision: LaunchDecision
    pid: Optional[int] = None
    status: str = ""
    expires_at: Optional[str] = None


LastLaunch = Optional[Union[datetime, float, int, str]]


def evaluate_launch(
    profile: AgentProfile,
    workspace: Union[Path, str],
    running_count: int,
    last_launch: LastLaunch,
    *,
    requested_auto: bool,
    now: Optional[datetime] = None,
) -> LaunchDecision:
    """Authorize only a locally configured automatic launch.

    The request supplies no command, environment, or working directory.  Those
    values are all derived from the target's stored profile and project path.
    """
    if profile.execution_policy is ExecutionPolicy.MANUAL:
        return LaunchDecision(False, "target policy is manual")
    if profile.execution_policy is ExecutionPolicy.PROMPT:
        return LaunchDecision(False, "target policy requires local approval")
    if not requested_auto:
        return LaunchDecision(False, "launch was not locally requested")
    resolved_workspace = _workspace(workspace)
    _require_allowed_workspace(profile, resolved_workspace)
    argv = _safe_argv(profile.launch_argv)
    if running_count < 0:
        raise LaunchPolicyError("running count cannot be negative")
    if running_count >= profile.max_concurrency:
        return LaunchDecision(False, "target concurrency limit reached")
    if _in_cooldown(last_launch, profile.cooldown_seconds, now):
        return LaunchDecision(False, "target cooldown is active")
    return LaunchDecision(True, "", argv, str(resolved_workspace))


def launch_agent(decision: LaunchDecision) -> LaunchResult:
    """Start an already-authorized argv without a shell or task-controlled state."""
    if not decision.allowed:
        return LaunchResult(False, decision.reason)
    if not decision.argv or not decision.workspace:
        return LaunchResult(False, "launch decision is incomplete")
    kwargs: dict[str, Any] = {
        "cwd": decision.workspace,
        "env": _minimal_environment(),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "shell": False,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(list(decision.argv), **kwargs)
    except (OSError, ValueError) as error:
        return LaunchResult(False, "launch failed: {0}".format(error))
    return LaunchResult(True, pid=process.pid)


class LaunchDeliveryChannel:
    """Pickleable dispatcher adapter which resolves policy in its child process."""

    effect_kind = "launch"

    def __init__(self, database_path: str) -> None:
        self.database_path = str(database_path)

    def applicable(self, item: Any) -> bool:
        """Skip manual/prompt targets before dispatcher creates an attempt row."""
        recipient = item.payload.get("recipient")
        if not isinstance(recipient, str) or not recipient:
            return True
        store = Store.open(Path(self.database_path))
        try:
            return load_agent_profile(store, recipient).execution_policy is ExecutionPolicy.AUTO
        except KeyError:
            return False
        except LaunchPolicyError:
            # A malformed local policy is an actionable delivery failure, not
            # evidence that the channel does not apply.
            return True
        finally:
            store.close()

    def deliver(self, item: Any, idempotency_key: str, timeout_seconds: float) -> Union[DeliveryStatus, DeferredDelivery]:
        """Launch the target once for a coalesced outbox representative."""
        del timeout_seconds
        task_id = item.payload.get("task_id")
        recipient = item.payload.get("recipient")
        if not isinstance(task_id, str) or not task_id or not isinstance(recipient, str) or not recipient:
            raise RuntimeError("launch item requires task_id and recipient")
        store = Store.open(Path(self.database_path))
        try:
            workspace = _task_workspace(store, task_id)
            result = launch_stored_agent(store, recipient, workspace, idempotency_key, task_id)
        finally:
            store.close()
        if not result.started:
            if result.pending_until is not None:
                return DeferredDelivery(result.pending_until, result.reason)
            raise RuntimeError(result.reason)
        return DeliveryStatus.LAUNCH_STARTED


def load_agent_profile(store: Store, name: str) -> AgentProfile:
    """Load the recipient-owned public profile, with a v1 DB fallback."""
    profile_path = store.path.parent / "agents" / name / "agent.json"
    receipt_path = profile_path.with_name("agent-bridge-profile.json")
    if profile_path.is_file() and receipt_path.is_file():
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            payload = json.loads(profile_path.read_text(encoding="utf-8"))
            owned_profile = Path(str(receipt.get("profile", ""))).resolve(strict=False)
            if receipt.get("owner") != "agent-bridge" or owned_profile != profile_path.resolve(strict=False) or payload.get("name") != name:
                raise ValueError("unowned public profile")
            return _profile_from_mapping(payload, name)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise LaunchPolicyError("invalid local launch profile") from error
    row = store.connection.execute("SELECT * FROM agents WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise KeyError("unknown agent: {0}".format(name))
    try:
        return _profile_from_mapping({
            "name": row["name"], "execution_policy": row["execution_policy"],
            "launch_argv": json.loads(row["launch_argv_json"]), "terminal_preference": row["terminal_preference"],
            "max_concurrency": row["max_concurrency"], "cooldown_seconds": row["cooldown_seconds"],
            "workspace_allowlist": json.loads(row["workspace_allowlist_json"]),
        }, name)
    except (TypeError, ValueError, OverflowError, json.JSONDecodeError) as error:
        raise LaunchPolicyError("invalid local launch profile") from error


def _profile_from_mapping(value: Any, expected_name: str) -> AgentProfile:
    if not isinstance(value, dict) or value.get("name") != expected_name:
        raise ValueError("profile identity mismatch")
    argv = _string_tuple(value.get("launch_argv"))
    allowlist = _string_tuple(value.get("workspace_allowlist"))
    policy = ExecutionPolicy(str(value.get("execution_policy")))
    terminal_preference = str(value.get("terminal_preference"))
    max_concurrency = int(value.get("max_concurrency"))
    cooldown_seconds = int(value.get("cooldown_seconds"))
    if max_concurrency < 1 or cooldown_seconds < 0 or terminal_preference not in ("auto", "integrated", "fallback"):
        raise ValueError("invalid launch limits")
    return AgentProfile(expected_name, policy, argv, terminal_preference, max_concurrency, cooldown_seconds, allowlist)


def launch_stored_agent(
    store: Store,
    name: str,
    workspace: Union[Path, str],
    idempotency_key: Optional[str] = None,
    task_id: Optional[str] = None,
) -> LaunchResult:
    """Reserve, then start one local target without an unprotected effect gap."""
    profile = load_agent_profile(store, name)
    resolved_workspace = str(_workspace(workspace))
    key = idempotency_key or "wake:{0}:{1}".format(name, resolved_workspace)
    reservation = _reserve_launch(store, profile, resolved_workspace, key, task_id)
    if not reservation.decision.allowed:
        return LaunchResult(False, reservation.decision.reason)
    if reservation.existing:
        if reservation.status == "started":
            return LaunchResult(True, "launch already started", reservation.pid)
        return LaunchResult(False, "launch reservation is pending", pending_until=reservation.expires_at)
    result = launch_agent(reservation.decision)
    if not result.started:
        _record_failure(store, key, result.reason)
        return result
    _record_started(store, key, result.pid)
    return result


def _task_workspace(store: Store, task_id: str) -> str:
    row = store.connection.execute(
        "SELECT projects.path FROM tasks JOIN projects ON projects.id = tasks.project_id WHERE tasks.id = ?",
        (task_id,),
    ).fetchone()
    if row is None:
        raise KeyError("unknown task: {0}".format(task_id))
    return str(row["path"])


def _last_launch(store: Store, name: str) -> Optional[str]:
    value = store.scalar(
        "SELECT MAX(COALESCE(started_at, reserved_at)) FROM launch_reservations "
        "WHERE agent_name = ? AND status IN ('reserved', 'started')",
        (name,),
    )
    return str(value) if value is not None else None


def _reserve_launch(
    store: Store,
    profile: AgentProfile,
    workspace: str,
    idempotency_key: str,
    task_id: Optional[str],
) -> _Reservation:
    """Atomically reserve one effect before a process can be started."""
    now = utc_now()
    with store.transaction(immediate=True) as connection:
        existing = connection.execute(
            "SELECT status, pid, expires_at FROM launch_reservations WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if existing is not None and str(existing["status"]) in ("reserved", "started") and str(existing["expires_at"]) > now:
            return _Reservation(
                True,
                LaunchDecision(True, "", _safe_argv(profile.launch_argv), workspace),
                existing["pid"],
                str(existing["status"]),
                str(existing["expires_at"]),
            )
        running_count = int(connection.execute(
            "SELECT COUNT(*) FROM launch_reservations WHERE agent_name = ? "
            "AND status IN ('reserved', 'started') AND expires_at > ?",
            (profile.name, now),
        ).fetchone()[0])
        decision = evaluate_launch(profile, workspace, running_count, _last_launch(store, profile.name), requested_auto=True)
        if not decision.allowed:
            return _Reservation(False, decision)
        expires_at = _reservation_expiry(profile.cooldown_seconds)
        connection.execute(
            "INSERT INTO launch_reservations("
            "idempotency_key, agent_name, task_id, workspace, status, pid, reserved_at, started_at, expires_at, error"
            ") VALUES (?, ?, ?, ?, 'reserved', NULL, ?, NULL, ?, NULL) "
            "ON CONFLICT(idempotency_key) DO UPDATE SET "
            "agent_name = excluded.agent_name, task_id = excluded.task_id, workspace = excluded.workspace, "
            "status = 'reserved', pid = NULL, reserved_at = excluded.reserved_at, started_at = NULL, "
            "expires_at = excluded.expires_at, error = NULL "
            "WHERE launch_reservations.expires_at <= excluded.reserved_at "
            "OR launch_reservations.status = 'failed'",
            (idempotency_key, profile.name, task_id, workspace, now, expires_at),
        )
        return _Reservation(False, decision)


def _record_started(store: Store, idempotency_key: str, pid: Optional[int]) -> None:
    with store.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE launch_reservations SET status = 'started', pid = ?, started_at = ?, error = NULL "
            "WHERE idempotency_key = ? AND status = 'reserved'",
            (pid, utc_now(), idempotency_key),
        )


def _record_failure(store: Store, idempotency_key: str, error: str) -> None:
    with store.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE launch_reservations SET status = 'failed', expires_at = ?, error = ? WHERE idempotency_key = ?",
            (utc_now(), error[:1000], idempotency_key),
        )


def _reservation_expiry(cooldown_seconds: int) -> str:
    seconds = max(RESERVATION_SECONDS, max(0, cooldown_seconds))
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _string_tuple(value: object) -> Tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("expected a JSON string list")
    return tuple(value)


def _workspace(value: Union[Path, str]) -> Path:
    workspace = Path(value).expanduser().resolve()
    if not workspace.is_dir():
        raise LaunchPolicyError("workspace must be an existing directory")
    return workspace


def _require_allowed_workspace(profile: AgentProfile, workspace: Path) -> None:
    if not profile.workspace_allowlist:
        raise LaunchPolicyError("workspace is not in the target allowlist")
    for allowed in profile.workspace_allowlist:
        allowed_path = Path(allowed).expanduser().resolve()
        try:
            workspace.relative_to(allowed_path)
        except ValueError:
            continue
        return
    raise LaunchPolicyError("workspace is not in the target allowlist")


def _safe_argv(argv: Sequence[str]) -> Tuple[str, ...]:
    if not argv:
        raise LaunchPolicyError("launch argv must not be empty")
    cleaned = tuple(argv)
    if any(not isinstance(value, str) or not value for value in cleaned):
        raise LaunchPolicyError("launch argv contains an empty argument")
    if any("\x00" in value or any(character in _SHELL_METACHARACTERS for character in value) for value in cleaned):
        raise LaunchPolicyError("launch argv contains shell metacharacters")
    return cleaned


def _in_cooldown(last_launch: LastLaunch, cooldown_seconds: int, now: Optional[datetime]) -> bool:
    if last_launch is None or cooldown_seconds <= 0:
        return False
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    previous = _launch_time(last_launch)
    return (current.astimezone(timezone.utc) - previous).total_seconds() < cooldown_seconds


def _launch_time(value: Union[datetime, float, int, str]) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (float, int)):
        parsed = datetime.fromtimestamp(value, tz=timezone.utc)
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise LaunchPolicyError("invalid last launch timestamp")
    if parsed.tzinfo is None:
        raise LaunchPolicyError("last launch timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _minimal_environment() -> dict[str, str]:
    names = ("PATH",) if os.name != "nt" else ("PATH", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP")
    return {name: os.environ[name] for name in names if os.environ.get(name)}
