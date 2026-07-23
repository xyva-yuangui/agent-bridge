from __future__ import annotations

import unittest

from agent_bridge.delivery import DeliveryAttempt, aggregate_delivery
from agent_bridge.models import DeliveryStatus


def attempt(channel: str, status: DeliveryStatus) -> DeliveryAttempt:
    return DeliveryAttempt(channel=channel, status=status)


class DeliveryAggregationTests(unittest.TestCase):
    def test_launch_is_weaker_than_acknowledgment(self) -> None:
        attempts = [
            attempt("launch", DeliveryStatus.LAUNCH_STARTED),
            attempt("notification", DeliveryStatus.OS_POSTED),
        ]
        self.assertEqual(aggregate_delivery(attempts), DeliveryStatus.LAUNCH_STARTED)

    def test_claim_is_strongest_evidence(self) -> None:
        attempts = [
            attempt("plugin", DeliveryStatus.PLUGIN_DELIVERED),
            attempt("agent", DeliveryStatus.CLAIMED),
        ]
        self.assertEqual(aggregate_delivery(attempts), DeliveryStatus.CLAIMED)

    def test_attempt_outcomes_do_not_hide_existing_evidence(self) -> None:
        attempts = [
            attempt("notification", DeliveryStatus.OS_POSTED),
            attempt("launcher", DeliveryStatus.FAILED),
            attempt("plugin", DeliveryStatus.RETRY_WAIT),
        ]
        self.assertEqual(aggregate_delivery(attempts), DeliveryStatus.OS_POSTED)


if __name__ == "__main__":
    unittest.main()
