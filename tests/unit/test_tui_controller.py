from __future__ import annotations

import io
import re
import unittest


class _Page:
    def __init__(self, tasks): self.tasks = tasks; self.next_cursor = None


class _Service:
    def __init__(self):
        self.calls = []
        self.task = {"id": "task-1", "sender": "codex", "assignee": "zcode", "subject": "Review", "body": "body", "state": "pending"}
    def agents(self): return ({"name": "zcode", "health": "ok"},)
    def board_page(self, project, limit=100, cursor=None): self.calls.append(("board", project, limit)); return _Page((self.task,))
    def delivery_evidence(self, task_id): return "plugin_delivered"
    def show(self, task_id): self.calls.append(("show", task_id)); return self.task
    def claim(self, task_id, actor): self.calls.append(("claim", task_id, actor)); return self.task
    def retry_delivery(self, task_id): self.calls.append(("retry", task_id)); return "queued"
    def open_terminal(self, task_id): self.calls.append(("open", task_id)); return "opened"


class _Output(io.StringIO):
    def __init__(self, tty): super().__init__(); self.tty = tty; self.supports_vt = tty
    def isatty(self): return self.tty


class _Input:
    supported = True
    def __init__(self, actions): self.actions = iter(actions); self.entered = self.exited = False
    def __enter__(self): self.entered = True; return self
    def __exit__(self, *ignored): self.exited = True
    def read_key(self, timeout): return next(self.actions)


class TuiControllerTests(unittest.TestCase):
    def test_redirected_output_is_one_noninteractive_compact_table(self) -> None:
        from agent_bridge.tui.controller import run_tui

        output = _Output(False)
        result = run_tui(_Service(), _Input([]), output)

        self.assertEqual(result, 0)
        self.assertIn("Agent Bridge tasks", output.getvalue())
        self.assertNotIn("\x1b[", output.getvalue())

    def test_non_vt_terminal_uses_compact_fallback(self) -> None:
        from agent_bridge.tui.controller import run_tui

        output = _Output(True)
        output.supports_vt = False
        self.assertEqual(run_tui(_Service(), _Input([]), output), 0)
        self.assertIn("Agent Bridge tasks", output.getvalue())
        self.assertNotIn("\x1b[", output.getvalue())

    def test_actions_use_public_service_methods_and_show_results(self) -> None:
        from agent_bridge.tui.controller import run_tui
        from agent_bridge.tui.input_common import Action

        service = _Service()
        output = _Output(True)
        adapter = _Input((Action.VIEW, Action.CLAIM, Action.RETRY, Action.OPEN, Action.QUIT))
        result = run_tui(service, adapter, output, actor="zcode")

        self.assertEqual(result, 0)
        self.assertTrue(adapter.exited)
        self.assertIn(("show", "task-1"), service.calls)
        self.assertIn(("claim", "task-1", "zcode"), service.calls)
        self.assertIn(("retry", "task-1"), service.calls)
        self.assertIn(("open", "task-1"), service.calls)
        self.assertIn("opened", output.getvalue())

    def test_ctrl_c_returns_130_and_restores_input(self) -> None:
        from agent_bridge.tui.controller import run_tui

        class Interrupting(_Input):
            def read_key(self, timeout): raise KeyboardInterrupt

        adapter = Interrupting(())
        self.assertEqual(run_tui(_Service(), adapter, _Output(True)), 130)
        self.assertTrue(adapter.exited)

    def test_action_result_is_bounded_to_the_terminal_width(self) -> None:
        from agent_bridge.tui.controller import run_tui
        from agent_bridge.tui.input_common import Action
        from agent_bridge.tui.render import display_width
        from unittest.mock import patch

        service = _Service()
        service.task["body"] = "x" * 1000
        output = _Output(True)
        with patch("agent_bridge.tui.controller._terminal_size", return_value=(40, 20)):
            run_tui(service, _Input((Action.CLAIM, Action.QUIT)), output, actor="zcode")
        plain = re.sub(r"\x1b\[[0-9;]*m", "", output.getvalue().replace("\x1b[2J\x1b[H", "\n"))
        self.assertTrue(all(display_width(line) <= 40 for line in plain.splitlines()))


if __name__ == "__main__":
    unittest.main()
