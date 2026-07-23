"""Channel-neutral delivery evidence and adapter contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol, Union

from .models import DeliveryStatus
from .outbox import OutboxItem


EVIDENCE_RANK = {
    DeliveryStatus.QUEUED: 0,
    DeliveryStatus.DISPATCHING: 1,
    DeliveryStatus.OS_POSTED: 2,
    DeliveryStatus.PLUGIN_DELIVERED: 3,
    DeliveryStatus.LAUNCH_STARTED: 4,
    DeliveryStatus.VIEWED: 5,
    DeliveryStatus.AGENT_ACKNOWLEDGED: 6,
    DeliveryStatus.CLAIMED: 7,
}


@dataclass(frozen=True)
class DeliveryAttempt:
    """One channel's durable evidence for an outbox intent."""

    channel: str
    status: DeliveryStatus
    attempts: int = 0
    error: str = ""


class DeliveryChannel(Protocol):
    """A pluggable channel invoked by the dispatcher for an outbox item."""

    def deliver(
        self, item: OutboxItem, idempotency_key: str, timeout_seconds: float
    ) -> DeliveryStatus:
        """Deliver bounded work and return only evidence actually established."""


AttemptLike = Union[DeliveryAttempt, Mapping[str, Any]]


def aggregate_delivery(attempts: Iterable[AttemptLike]) -> DeliveryStatus:
    """Return the strongest delivery evidence without hiding channel outcomes.

    ``retry_wait`` and ``failed`` are deliberately excluded from evidence
    precedence: they describe an individual attempt's outcome rather than proof
    that another channel's delivery did not happen.
    """
    strongest = DeliveryStatus.QUEUED
    for attempt in attempts:
        status = _attempt_status(attempt)
        if EVIDENCE_RANK.get(status, -1) > EVIDENCE_RANK[strongest]:
            strongest = status
    return strongest


def _attempt_status(attempt: AttemptLike) -> DeliveryStatus:
    value = attempt.status if isinstance(attempt, DeliveryAttempt) else attempt["status"]
    return value if isinstance(value, DeliveryStatus) else DeliveryStatus(str(value))
