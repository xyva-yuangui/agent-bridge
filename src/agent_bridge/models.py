"""Shared immutable data models for Agent Bridge v2."""

import enum
from dataclasses import dataclass
from typing import Tuple


class TaskState(str, enum.Enum):
    PENDING = "pending"
    WORKING = "working"
    INPUT_REQUIRED = "input_required"
    REVIEW_REQUESTED = "review_requested"
    CHANGES_REQUESTED = "changes_requested"
    COMPLETED = "completed"
    FAILED = "failed"


class DeliveryStatus(str, enum.Enum):
    QUEUED = "queued"
    DISPATCHING = "dispatching"
    OS_POSTED = "os_posted"
    PLUGIN_DELIVERED = "plugin_delivered"
    VIEWED = "viewed"
    LAUNCH_STARTED = "launch_started"
    AGENT_ACKNOWLEDGED = "agent_acknowledged"
    CLAIMED = "claimed"
    RETRY_WAIT = "retry_wait"
    FAILED = "failed"


class ExecutionPolicy(str, enum.Enum):
    MANUAL = "manual"
    PROMPT = "prompt"
    AUTO = "auto"


@dataclass(frozen=True)
class AgentProfile:
    name: str
    execution_policy: ExecutionPolicy = ExecutionPolicy.MANUAL
    launch_argv: Tuple[str, ...] = ()
    terminal_preference: str = "auto"
    max_concurrency: int = 1
    cooldown_seconds: int = 30
    workspace_allowlist: Tuple[str, ...] = ()
