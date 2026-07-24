from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_bridge.models import DeliveryStatus
from agent_bridge.cli import execute_command, _owned_windows_notification_configuration
from agent_bridge.notifications import NotificationNotice, WindowsNotificationChannel, WindowsNotifier, windows_notification_capability
from agent_bridge.outbox import OutboxItem
from agent_bridge.service import BridgeService
from agent_bridge.store import Store
from agent_bridge.terminals import OpenResult


class WindowsNotifyProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.notice = NotificationNotice(
            title="Task ready",
            body="Read this safely",
            task_id="task-opaque-123",
            expires_in_seconds=30,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def helper(self, source: str) -> Path:
        path = self.directory / "helper.cmd"
        encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
        command = "import base64;exec(base64.b64decode('" + encoded + "'))"
        path.write_text("@\"" + sys.executable + "\" -c \"" + command + "\"\r\n", encoding="utf-8")
        return path

    def test_post_result_requires_native_identifier(self) -> None:
        helper = self.helper("import sys; print('{\\\"ok\\\":true,\\\"status\\\":\\\"os_posted\\\"}')")

        result = WindowsNotifier(helper).post(self.notice)

        self.assertFalse(result.ok)
        self.assertEqual(result.status, DeliveryStatus.FAILED)
        self.assertIn("notification_id", result.detail)

    def test_timeout_is_visible_failure(self) -> None:
        helper = self.helper("import time; time.sleep(2)")

        result = WindowsNotifier(helper, timeout_seconds=0.1).post(self.notice)

        self.assertFalse(result.ok)
        self.assertEqual(result.status, DeliveryStatus.FAILED)
        self.assertIn("timed out", result.detail)

    def test_rejects_malformed_or_oversized_helper_output(self) -> None:
        malformed = self.helper("print('not-json')")
        oversized = self.helper("print('x' * 8193)")

        self.assertEqual(WindowsNotifier(malformed).post(self.notice).status, DeliveryStatus.FAILED)
        self.assertIn("malformed", WindowsNotifier(malformed).post(self.notice).detail)
        self.assertIn("too large", WindowsNotifier(oversized, max_output_bytes=8192).post(self.notice).detail)

    def test_serializes_only_bounded_opaque_action_protocol(self) -> None:
        capture = self.directory / "request.json"
        source = (
            "import pathlib,sys; pathlib.Path(r'" + str(capture).replace("\\", "\\\\") + "').write_text(sys.stdin.read(), encoding='utf-8'); "
            "print('{\\\"ok\\\":true,\\\"notification_id\\\":\\\"native-1\\\",\\\"status\\\":\\\"os_posted\\\",\\\"detail\\\":\\\"posted\\\"}')"
        )

        result = WindowsNotifier(self.helper(source)).post(self.notice)

        self.assertTrue(result.ok)
        payload = capture.read_text(encoding="utf-8")
        self.assertIn('"operation":"post"', payload)
        self.assertIn('"actions":["view","claim","snooze"]', payload)
        self.assertIn('"task_id":"task-opaque-123"', payload)
        self.assertNotIn("cmd", payload.lower())

    def test_delivery_persists_native_id_only_after_os_accepts_post(self) -> None:
        store = Store.open(self.directory / "agent-bridge.sqlite3")
        try:
            task = BridgeService(store).send_task("sender", "receiver", "Native toast", "body")
            item = OutboxItem(1, "delivery-1", "task.created", {"task_id": task.id}, "2026-01-01T00:00:00Z", 0)
            helper = self.helper("print('{\\\"ok\\\":true,\\\"notification_id\\\":\\\"native-1\\\",\\\"status\\\":\\\"os_posted\\\",\\\"detail\\\":\\\"posted\\\"}')")

            status = WindowsNotificationChannel(store.path, helper).deliver(item, "delivery-1", 1.0)

            self.assertEqual(status, DeliveryStatus.OS_POSTED)
            self.assertEqual(store.scalar("SELECT task_id FROM notification_mappings WHERE notification_id = ?", ("native-1",)), task.id)
        finally:
            store.close()

    def test_missing_helper_reports_degraded_capability_without_claiming_delivery(self) -> None:
        previous = os.environ.pop("AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER", None)
        try:
            capability = windows_notification_capability()
        finally:
            if previous is not None:
                os.environ["AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER"] = previous

        self.assertFalse(capability.available)
        self.assertIn("not installed", capability.detail)

    def test_dispatch_discovers_the_owned_helper_without_an_environment_refresh(self) -> None:
        data_root = self.directory / "owned-data"
        helper = data_root / "native" / "agent-bridge-windows-notify.exe"
        helper.parent.mkdir(parents=True)
        helper.write_bytes(b"owned helper fixture")
        activation = [
            sys.executable,
            str(data_root / "skill" / "scripts" / "bridge.py"),
            "--as",
            "notification-action",
            "--data-root",
            str(data_root),
        ]
        (helper.parent / "receipt.json").write_text(
            json.dumps(
                {
                    "owner": "agent-bridge.windows-notify",
                    "helper_path": str(helper),
                    "sha256": hashlib.sha256(helper.read_bytes()).hexdigest(),
                    "activation_argv": activation,
                }
            ),
            encoding="utf-8",
        )

        discovered, issue = _owned_windows_notification_configuration(data_root)

        self.assertEqual(helper.resolve(), discovered)
        self.assertEqual("", issue)

    def test_doctor_exposes_native_notification_degradation(self) -> None:
        store = Store.open(self.directory / "doctor.sqlite3")
        previous = os.environ.pop("AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER", None)
        try:
            report = execute_command(BridgeService(store), "codex", "doctor", {"strict": True})
        finally:
            store.close()
            if previous is not None:
                os.environ["AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER"] = previous

        self.assertIn("native_notifications", report["checks"])
        self.assertFalse(report["checks"]["native_notifications"])
        self.assertIn("notification_capability", report)

    def test_dispatch_uses_configured_native_helper_and_records_evidence(self) -> None:
        store = Store.open(self.directory / "dispatch.sqlite3")
        helper = self.helper("print('{\\\"ok\\\":true,\\\"notification_id\\\":\\\"native-dispatch\\\",\\\"status\\\":\\\"os_posted\\\",\\\"detail\\\":\\\"posted\\\"}')")
        old = os.environ.get("AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER")
        os.environ["AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER"] = str(helper)
        try:
            task = BridgeService(store).send_task("sender", "receiver", "Dispatch toast", "body")
            report = execute_command(BridgeService(store), "sender", "dispatch", {"burst": True})
        finally:
            if old is None:
                os.environ.pop("AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER", None)
            else:
                os.environ["AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER"] = old
            store.close()

        self.assertGreaterEqual(report["dispatch"]["delivered"], 1)
        verify = Store.open(self.directory / "dispatch.sqlite3")
        try:
            self.assertEqual(verify.scalar("SELECT task_id FROM notification_mappings WHERE notification_id = ?", ("native-dispatch",)), task.id)
        finally:
            verify.close()

    def test_open_action_uses_stored_notification_mapping_not_supplied_task_id(self) -> None:
        store = Store.open(self.directory / "action.sqlite3")
        try:
            task = BridgeService(store).send_task("sender", "receiver", "Mapped action", "body")
            with store.transaction(immediate=True) as connection:
                connection.execute(
                    "INSERT INTO notification_mappings(notification_id, task_id, action, created_at) VALUES (?, ?, ?, ?)",
                    ("native-action", task.id, "view", "2026-01-01T00:00:00Z"),
                )
            with patch("agent_bridge.dispatcher.tick", return_value=False):
                result = execute_command(BridgeService(store), "receiver", "open-action", {"notification_id": "native-action", "action": "view"})
        finally:
            store.close()

        self.assertEqual(result["open_action"]["task"]["id"], task.id)

    def test_notification_actions_claim_snooze_and_open_the_mapped_task(self) -> None:
        store = Store.open(self.directory / "all-actions.sqlite3")
        try:
            service = BridgeService(store)
            viewed = service.send_task("sender", "receiver", "View", "body")
            claimed = service.send_task("sender", "receiver", "Claim", "body")
            snoozed = service.send_task("sender", "receiver", "Snooze", "body")
            with store.transaction(immediate=True) as connection:
                connection.executemany(
                    "INSERT INTO notification_mappings(notification_id, task_id, action, created_at) "
                    "VALUES (?, ?, 'view', '2026-01-01T00:00:00Z')",
                    (
                        ("notice-view", viewed.id),
                        ("notice-claim", claimed.id),
                        ("notice-snooze", snoozed.id),
                    ),
                )
            with patch("agent_bridge.dispatcher.tick", return_value=False), patch(
                "agent_bridge.dispatcher.request_dispatch", return_value=True
            ) as dispatch_requested, patch.object(
                BridgeService,
                "open_terminal",
                return_value=OpenResult(True, "windows-terminal", pid=7),
            ) as opened:
                view_result = execute_command(
                    service,
                    "untrusted-caller",
                    "open-action",
                    {"notification_id": "notice-view", "action": "view"},
                )
                claim_result = execute_command(
                    service,
                    "untrusted-caller",
                    "open-action",
                    {"notification_id": "notice-claim", "action": "claim"},
                )
                snooze_result = execute_command(
                    service,
                    "untrusted-caller",
                    "open-action",
                    {"notification_id": "notice-snooze", "action": "snooze"},
                )

            opened.assert_called_once_with(viewed.id)
            self.assertEqual("receiver", view_result["open_action"]["actor"])
            self.assertEqual("working", claim_result["open_action"]["task"]["state"])
            self.assertEqual("receiver", claim_result["open_action"]["actor"])
            dispatch_requested.assert_called_once_with(store.path.parent)
            self.assertEqual(
                "retry_wait", snooze_result["open_action"]["delivery"]["status"]
            )
            due_at = store.scalar(
                "SELECT MAX(due_at) FROM outbox "
                "WHERE json_extract(payload_json, '$.task_id') = ?",
                (snoozed.id,),
            )
            self.assertGreater(str(due_at), "2026-01-01T00:00:00Z")
        finally:
            store.close()

    def test_windows_registration_source_restores_prior_owners_and_checks_current_ownership(self) -> None:
        source = (
            Path(__file__).resolve().parents[2]
            / "native"
            / "windows-notify"
            / "src"
            / "registration.rs"
        ).read_text(encoding="utf-8")
        for token in (
            "PreviousProtocolJson",
            "verify_owned_registration()?",
            "restore_registration(&classes)?",
            "restore_start_menu_shortcut()?",
            "refusing to remove a protocol no longer owned by Agent Bridge",
        ):
            self.assertIn(token, source)


if __name__ == "__main__":
    unittest.main()
