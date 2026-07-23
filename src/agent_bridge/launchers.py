"""Local policy checks and argv-safe agent process launching."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence, Tuple, Union

from .models import AgentProfile, DeliveryStatus, ExecutionPolicy
from .store import Store


_SHELL_METACHARACTERS = frozenset("|&;<>()$`\r\n%^!")


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
    except OSError as error:
        return LaunchResult(False, "launch failed: {0}".format(error))
    return LaunchResult(True, pid=process.pid)


class LaunchDeliveryChannel:
    """Pickleable dispatcher adapter which resolves policy in its child process."""

    def __init__(self, database_path: str) -> None:
        self.database_path = str(database_path)

    def deliver(self, item: Any, idempotency_key: str, timeout_seconds: float) -> DeliveryStatus:
        """Launch the target once for a coalesced outbox representative."""
        del idempotency_key, timeout_seconds
        task_id = item.payload.get("task_id")
        recipient = item.payload.get("recipient")
        if not isinstance(task_id, str) or not task_id or not isinstance(recipient, str) or not recipient:
            raise RuntimeError("launch item requires task_id and recipient")
        store = Store.open(Path(self.database_path))
        try:
            profile = load_agent_profile(store, recipient)
            workspace = _task_workspace(store, task_id)
            running_count = int(store.scalar(
                "SELECT COUNT(*) FROM tasks WHERE assignee = ? AND state = 'working'", (recipient,)
            ) or 0)
            last_launch = _last_launch(store, recipient)
        finally:
            store.close()
        decision = evaluate_launch(profile, workspace, running_count, last_launch, requested_auto=True)
        if not decision.allowed:
            raise RuntimeError(decision.reason)
        result = launch_agent(decision)
        if not result.started:
            raise RuntimeError(result.reason)
        return DeliveryStatus.LAUNCH_STARTED


def load_agent_profile(store: Store, name: str) -> AgentProfile:
    """Load only locally persisted target configuration into an immutable profile."""
    row = store.connection.execute("SELECT * FROM agents WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise KeyError("unknown agent: {0}".format(name))
    try:
        argv = _string_tuple(json.loads(row["launch_argv_json"]))
        allowlist = _string_tuple(json.loads(row["workspace_allowlist_json"]))
        policy = ExecutionPolicy(str(row["execution_policy"]))
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise LaunchPolicyError("invalid local launch profile") from error
    return AgentProfile(
        name=str(row["name"]),
        execution_policy=policy,
        launch_argv=argv,
        terminal_preference=str(row["terminal_preference"]),
        max_concurrency=int(row["max_concurrency"]),
        cooldown_seconds=int(row["cooldown_seconds"]),
        workspace_allowlist=allowlist,
    )


def launch_stored_agent(store: Store, name: str, workspace: Union[Path, str]) -> LaunchResult:
    """Evaluate and launch one named target using only data in the local store."""
    profile = load_agent_profile(store, name)
    running_count = int(store.scalar(
        "SELECT COUNT(*) FROM tasks WHERE assignee = ? AND state = 'working'", (name,)
    ) or 0)
    last_launch = _last_launch(store, name)
    decision = evaluate_launch(profile, workspace, running_count, last_launch, requested_auto=True)
    return launch_agent(decision)


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
        "SELECT MAX(delivery_attempts.updated_at) FROM delivery_attempts "
        "JOIN tasks ON tasks.id = delivery_attempts.task_id "
        "WHERE tasks.assignee = ? AND delivery_attempts.channel = 'launcher' "
        "AND delivery_attempts.status = 'launch_started'",
        (name,),
    )
    return str(value) if value is not None else None


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
    if any(any(character in _SHELL_METACHARACTERS for character in value) for value in cleaned):
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
