from __future__ import annotations

import tempfile
import unittest
import io
from unittest.mock import patch
from pathlib import Path

from agent_bridge.service import BridgeService
from agent_bridge.store import Store


class TuiServiceApiTests(unittest.TestCase):
    def test_tui_queries_are_bounded_public_service_views(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Store.open(Path(directory) / "bridge.sqlite3")
            service = BridgeService(store)
            try:
                task = service.send_task("codex", "zcode", "Review", "body")
                page = service.board_page("default", limit=1)

                self.assertEqual(page.tasks[0].id, task.id)
                self.assertIsNone(page.next_cursor)
                self.assertEqual(service.agents()[0].name, "codex")
                self.assertEqual(service.delivery_evidence(task.id).status, "queued")
                self.assertEqual(service.retry_delivery(task.id).status, "queued")
            finally:
                store.close()

    def test_delivery_evidence_keeps_the_strongest_proven_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Store.open(Path(directory) / "bridge.sqlite3")
            service = BridgeService(store)
            try:
                task = service.send_task("codex", "zcode", "Review", "body")
                with store.transaction(immediate=True) as connection:
                    connection.execute("INSERT INTO delivery_attempts(task_id, channel, status, attempts, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)", (task.id, "host", "plugin_delivered", 2, "2020", "2020"))
                    connection.execute("INSERT INTO delivery_attempts(task_id, channel, status, attempts, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)", (task.id, "retry", "retry_wait", 1, "2021", "2021"))
                evidence = service.delivery_evidence(task.id)
                self.assertEqual(evidence.status, "plugin_delivered")
                self.assertEqual(evidence.attempts, 3)
            finally:
                store.close()

    def test_compact_tui_accepts_service_agent_views_without_storage_access(self) -> None:
        from agent_bridge.tui.controller import run_tui

        class Input:
            supported = False

        with tempfile.TemporaryDirectory() as directory:
            store = Store.open(Path(directory) / "bridge.sqlite3")
            service = BridgeService(store)
            try:
                service.send_task("codex", "zcode", "Review", "body")
                output = io.StringIO()
                self.assertEqual(run_tui(service, Input(), output), 0)
                self.assertIn("Review", output.getvalue())
            finally:
                store.close()

    def test_interactive_tui_renders_service_agent_views(self) -> None:
        from agent_bridge.tui.controller import run_tui
        from agent_bridge.tui.input_common import Action

        class Output(io.StringIO):
            def isatty(self): return True
            supports_vt = True
        class Input:
            supported = True
            def __enter__(self): return self
            def __exit__(self, *ignored): return False
            def read_key(self, timeout): return Action.QUIT

        with tempfile.TemporaryDirectory() as directory:
            store = Store.open(Path(directory) / "bridge.sqlite3")
            service = BridgeService(store)
            try:
                service.send_task("codex", "zcode", "Review", "body")
                output = Output()
                with patch("agent_bridge.tui.controller._terminal_size", return_value=(120, 24)):
                    self.assertEqual(run_tui(service, Input(), output), 0)
                self.assertIn("inbox 1", output.getvalue())
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
