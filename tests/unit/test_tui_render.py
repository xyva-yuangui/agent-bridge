from __future__ import annotations

import unittest

from agent_bridge.tui.model import build_dashboard


class DashboardRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dashboard = build_dashboard({"agents": [{"name": "zcode", "health": "ok"}], "tasks": [{
            "id": "49d05a", "sender": "codex", "assignee": "zcode", "state": "pending",
            "subject": "A long subject that must remain bounded", "body": "A very long body " * 20,
            "delivery": "plugin_delivered",
        }]})

    def test_narrow_terminal_uses_stacked_layout(self) -> None:
        from agent_bridge.tui.render import render_dashboard

        screen = render_dashboard(self.dashboard, width=79, height=24)

        self.assertNotIn("\x1b[999", screen)
        self.assertIn("#49d05a", screen)
        self.assertIn("Details", screen)

    def test_no_color_and_compact_output_never_emit_control_sequences(self) -> None:
        from agent_bridge.tui.render import render_compact, render_dashboard

        self.assertNotIn("\x1b[", render_dashboard(self.dashboard, 120, 20, color=False))
        self.assertNotIn("\x1b[", render_compact(self.dashboard, width=40))

    def test_unicode_display_cells_and_long_fields_are_bounded(self) -> None:
        from agent_bridge.tui.render import display_width, render_dashboard

        screen = render_dashboard(self.dashboard, width=40, height=12, color=False)
        self.assertTrue(all(display_width(line) <= 40 for line in screen.splitlines()))
        self.assertIn("…", screen)

    def test_wide_columns_fit_the_available_terminal_cells(self) -> None:
        from agent_bridge.tui.render import display_width, render_dashboard

        screen = render_dashboard(self.dashboard, width=120, height=20, color=False)
        self.assertTrue(all(display_width(line) <= 120 for line in screen.splitlines()))


if __name__ == "__main__":
    unittest.main()
