from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_bridge.terminals import open_task_terminal


class IntegratedHost:
    supports_integrated_terminal = True

    def __init__(self) -> None:
        self.calls = []

    def open_integrated_terminal(self, argv, workspace):
        self.calls.append((argv, workspace))
        return True


class TerminalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self.directory.name)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_host_integrated_terminal_is_preferred(self) -> None:
        host = IntegratedHost()

        result = open_task_terminal(host, "task; 123", self.workspace)

        self.assertTrue(result.opened)
        self.assertEqual(result.method, "host")
        argv, workspace = host.calls[0]
        self.assertIn("task; 123", argv)
        self.assertEqual(workspace, str(self.workspace.resolve()))

    @patch("agent_bridge.terminals._is_windows", return_value=True)
    @patch("agent_bridge.terminals.subprocess.Popen")
    def test_windows_terminal_is_used_after_no_host_terminal(self, popen, ignored_windows) -> None:
        popen.return_value.pid = 11

        result = open_task_terminal(None, "task-123", self.workspace)

        self.assertTrue(result.opened)
        self.assertEqual(result.method, "windows-terminal")
        argv = popen.call_args.args[0]
        self.assertEqual(argv[0], "wt.exe")
        self.assertEqual(argv[2], str(self.workspace.resolve()))
        self.assertIn("task-123", argv)
        self.assertFalse(popen.call_args.kwargs["shell"])

    @patch("agent_bridge.terminals._is_macos", return_value=True)
    @patch("agent_bridge.terminals._is_windows", return_value=False)
    @patch("agent_bridge.terminals.subprocess.Popen")
    def test_macos_terminal_uses_structured_open_arguments(self, popen, ignored_windows, ignored_macos) -> None:
        popen.return_value.pid = 12

        result = open_task_terminal(None, "task; 123", self.workspace)

        self.assertTrue(result.opened)
        self.assertEqual(result.method, "macos-terminal")
        argv = popen.call_args.args[0]
        self.assertEqual(argv[:2], ["osascript", "-e"])
        self.assertIn(str(self.workspace.resolve()), argv)
        self.assertIn("task; 123", argv)
        self.assertFalse(popen.call_args.kwargs["shell"])

    @patch("agent_bridge.terminals._is_macos", return_value=False)
    @patch("agent_bridge.terminals._is_windows", return_value=False)
    def test_plain_instructions_are_the_final_fallback(self, ignored_windows, ignored_macos) -> None:
        result = open_task_terminal(None, "task; 123", self.workspace)

        self.assertFalse(result.opened)
        self.assertEqual(result.method, "instructions")
        self.assertIn("'task; 123'", result.instructions)
        self.assertNotIn("show task; 123", result.instructions)
        self.assertIn(str(self.workspace.resolve()), result.instructions)
