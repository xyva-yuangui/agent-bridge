from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_bridge.dispatcher import tick
from agent_bridge.service import BridgeService
from agent_bridge.store import Store
from agent_bridge.tui.model import build_dashboard


TASK_COUNT = 1_000
CI_MULTIPLIER = 2.0
BUDGET_SECONDS = {
    "create": 0.050,
    "inbox": 0.100,
    "tick": 0.050,
    "tui_projection": 0.100,
}


def _p95(samples: list[float]) -> float:
    return sorted(samples)[max(0, int(len(samples) * 0.95 + 0.999999) - 1)]


class PerformanceBudgetTests(unittest.TestCase):
    def test_one_thousand_operation_percentiles_stay_within_documented_ci_multiplier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Store.open(Path(directory) / "agent-bridge.sqlite3")
            try:
                service = BridgeService(store)
                creates = [self._elapsed(lambda index=index: service.send_task("codex", "claude", "perf-{0}".format(index), "body")) for index in range(TASK_COUNT)]
                inbox = [self._elapsed(lambda: service.inbox("claude")) for ignored in range(TASK_COUNT)]
                with store.transaction(immediate=True) as connection:
                    connection.execute("UPDATE outbox SET completed_at = '2099-01-01T00:00:00Z'")
                with patch("agent_bridge.dispatcher.request_dispatch", return_value=True):
                    ticks = [self._elapsed(lambda: tick(store)) for ignored in range(TASK_COUNT)]
                snapshot = {"agents": service.agents(), "tasks": service.board_page("default", limit=100).tasks}
                projections = [self._elapsed(lambda: build_dashboard(snapshot)) for ignored in range(TASK_COUNT)]
                metrics = {"create": _p95(creates), "inbox": _p95(inbox), "tick": _p95(ticks), "tui_projection": _p95(projections)}
                print("TASK12_PERFORMANCE " + " ".join("{0}_p95_ms={1:.3f}".format(key, value * 1000) for key, value in metrics.items()) + " ci_multiplier={0:.1f}".format(CI_MULTIPLIER))
                for name, p95 in metrics.items():
                    self.assertLessEqual(p95, BUDGET_SECONDS[name] * CI_MULTIPLIER, name)
            finally:
                store.close()

    @staticmethod
    def _elapsed(operation) -> float:
        started = time.perf_counter()
        operation()
        return time.perf_counter() - started


if __name__ == "__main__":
    unittest.main()
