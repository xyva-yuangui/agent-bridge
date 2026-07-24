from __future__ import annotations

import unittest


class DashboardModelTests(unittest.TestCase):
    def test_dashboard_contains_agents_counts_tasks_and_details(self) -> None:
        from agent_bridge.tui.model import build_dashboard

        view = build_dashboard({
            "agents": [{"name": "codex"}, {"name": "claude"}, {"name": "reasonix"}, {"name": "zcode"}],
            "tasks": [{"id": "task-1", "sender": "codex", "assignee": "zcode", "subject": "Review", "body": "Please review", "state": "review_requested", "delivery": "plugin_delivered"}],
        })

        self.assertEqual(len(view.agents), 4)
        self.assertEqual(view.counts.review, 1)
        self.assertEqual(view.selected_task.delivery, "plugin_delivered")


if __name__ == "__main__":
    unittest.main()
