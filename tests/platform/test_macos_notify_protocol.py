from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from agent_bridge.cli import execute_command
from agent_bridge.models import DeliveryStatus
from agent_bridge.notifications import (
    MacOSSigningAssessment,
    MacOSNotificationChannel,
    MacOSNotifier,
    NotificationNotice,
    macos_notification_capability,
)
from agent_bridge.outbox import OutboxItem
from agent_bridge.service import BridgeService
from agent_bridge.store import Store


class MacOSNotifyProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.notice = NotificationNotice("Task ready", "Read this safely", "task-opaque-123", 30)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def helper(self, source: str) -> Path:
        path = self.directory / "helper.cmd"
        encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
        command = "import base64;exec(base64.b64decode('" + encoded + "'))"
        path.write_text('@"' + sys.executable + '" -c "' + command + '" %*\r\n', encoding="utf-8")
        return path

    def test_post_requires_a_complete_native_response(self) -> None:
        result = MacOSNotifier(self.helper("print('{\\\"ok\\\":true,\\\"status\\\":\\\"os_posted\\\"}')")).post(self.notice)

        self.assertFalse(result.ok)
        self.assertEqual(result.status, DeliveryStatus.FAILED)
        self.assertIn("notification_id", result.detail)

    def test_post_uses_the_fixed_bounded_protocol_without_shell_data(self) -> None:
        capture = self.directory / "request.json"
        source = (
            "import pathlib,sys; pathlib.Path(r'" + str(capture).replace("\\", "\\\\") + "').write_text(sys.stdin.read(), encoding='utf-8'); "
            "print('{\\\"ok\\\":true,\\\"notification_id\\\":\\\"mac-native-1\\\",\\\"status\\\":\\\"os_posted\\\",\\\"detail\\\":\\\"posted\\\"}')"
        )

        result = MacOSNotifier(self.helper(source)).post(self.notice)

        self.assertTrue(result.ok)
        self.assertEqual(json.loads(capture.read_text(encoding="utf-8")), {
            "operation": "post", "title": "Task ready", "body": "Read this safely", "task_id": "task-opaque-123",
            "actions": ["view", "claim", "snooze"], "expires_in_seconds": 30,
        })

    def test_python_client_always_selects_explicit_protocol_mode(self) -> None:
        capture = self.directory / "argv.json"
        source = (
            "import json,pathlib,sys; pathlib.Path(r'" + str(capture).replace("\\", "\\\\") + "').write_text(json.dumps(sys.argv[1:])); "
            "print('{\\\"ok\\\":true,\\\"notification_id\\\":\\\"mac-native-argv\\\",\\\"status\\\":\\\"os_posted\\\",\\\"detail\\\":\\\"posted\\\"}')"
        )

        self.assertTrue(MacOSNotifier(self.helper(source)).post(self.notice).ok)

        self.assertEqual(json.loads(capture.read_text()), ["--protocol"])

    def test_register_requires_an_absolute_fixed_argv(self) -> None:
        helper = self.helper("print('{\\\"ok\\\":true,\\\"notification_id\\\":\\\"registration\\\",\\\"status\\\":\\\"os_posted\\\",\\\"detail\\\":\\\"registered\\\"}')")

        self.assertFalse(MacOSNotifier(helper).register(["bridge", "open-action"]).ok)
        self.assertTrue(MacOSNotifier(helper).register([str(Path(sys.executable).resolve()), "open-action"]).ok)

    def test_delivery_persists_only_the_helper_opaque_identifier(self) -> None:
        store = Store.open(self.directory / "agent-bridge.sqlite3")
        try:
            task = BridgeService(store).send_task("sender", "receiver", "Native notice", "body")
            item = OutboxItem(1, "delivery-1", "task.created", {"task_id": task.id}, "2026-01-01T00:00:00Z", 0)
            helper = self.helper("print('{\\\"ok\\\":true,\\\"notification_id\\\":\\\"mac-native-1\\\",\\\"status\\\":\\\"os_posted\\\",\\\"detail\\\":\\\"posted\\\"}')")

            status = MacOSNotificationChannel(store.path, helper, [str(Path(sys.executable).resolve()), "open-action"]).deliver(item, "delivery-1", 1.0)

            self.assertEqual(status, DeliveryStatus.OS_POSTED)
            self.assertEqual(store.scalar("SELECT task_id FROM notification_mappings WHERE notification_id = ?", ("mac-native-1",)), task.id)
        finally:
            store.close()

    def test_channel_registers_fixed_argv_before_post_when_status_is_unregistered(self) -> None:
        capture = self.directory / "operations.json"
        source = (
            "import json,pathlib,sys; p=pathlib.Path(r'" + str(capture).replace("\\", "\\\\") + "'); "
            "ops=json.loads(p.read_text()) if p.exists() else []; request=json.loads(sys.stdin.read()); ops.append(request['operation']); p.write_text(json.dumps(ops)); "
            "response=({'ok':False,'notification_id':'','status':'failed','detail':'fixed activation argv is not registered'} if request['operation']=='status' and ops.count('status')==1 else "
            "{'ok':True,'notification_id':('mac-native-2' if request['operation']=='post' else 'registration'),'status':'os_posted','detail':('expiration scheduled for 30 seconds' if request['operation']=='post' else 'ready')}); print(json.dumps(response))"
        )
        store = Store.open(self.directory / "registration.sqlite3")
        try:
            task = BridgeService(store).send_task("sender", "receiver", "Register", "body")
            item = OutboxItem(1, "delivery-registration", "task.created", {"task_id": task.id}, "2026-01-01T00:00:00Z", 0)
            channel = MacOSNotificationChannel(store.path, self.helper(source), [str(Path(sys.executable).resolve()), "open-action"])

            self.assertEqual(channel.deliver(item, "delivery-registration", 1.0), DeliveryStatus.OS_POSTED)

            self.assertEqual(json.loads(capture.read_text()), ["status", "register", "status", "post"])
        finally:
            store.close()

    def test_long_expiry_does_not_claim_native_expiration_without_an_honest_helper_response(self) -> None:
        helper = self.helper("print('{\\\"ok\\\":true,\\\"notification_id\\\":\\\"mac-native-3\\\",\\\"status\\\":\\\"os_posted\\\",\\\"detail\\\":\\\"expiration scheduled for 31 seconds\\\"}')")

        result = MacOSNotifier(helper).post(NotificationNotice("Task", "Body", "task-opaque-3", 31))

        self.assertFalse(result.ok)
        self.assertIn("expiration", result.detail)

    def test_capability_and_doctor_degrade_honestly_off_macos(self) -> None:
        helper = self.helper("print('{\\\"ok\\\":true,\\\"notification_id\\\":\\\"registration\\\",\\\"status\\\":\\\"os_posted\\\",\\\"detail\\\":\\\"ready\\\"}')")
        with patch.dict(os.environ, {"AGENT_BRIDGE_MACOS_NOTIFY_HELPER": str(helper)}), patch("agent_bridge.notifications.sys.platform", "win32"):
            capability = macos_notification_capability()

        self.assertFalse(capability.available)
        self.assertIn("unavailable", capability.detail)
        store = Store.open(self.directory / "doctor.sqlite3")
        try:
            with patch("agent_bridge.cli.sys.platform", "darwin"):
                report = execute_command(BridgeService(store), "codex", "doctor", {"strict": True})
        finally:
            store.close()
        self.assertIn("native_notifications", report["checks"])
        self.assertFalse(report["checks"]["native_notifications"])

    def test_strict_doctor_separates_an_unsigned_helper_from_local_notification_availability(self) -> None:
        helper = self.helper("print('{\\\"ok\\\":true,\\\"notification_id\\\":\\\"registration\\\",\\\"status\\\":\\\"os_posted\\\",\\\"detail\\\":\\\"ready\\\"}')")
        store = Store.open(self.directory / "signing.sqlite3")
        capability = type("Capability", (), {"available": True, "helper_path": str(helper), "detail": "ready", "expiry_detail": "no native expiry", "signing_status": "unsigned", "gatekeeper": "unknown"})()
        assessment = MacOSSigningAssessment("unsigned", "codesign reports no signature", "notarization unknown")
        try:
            with patch("agent_bridge.cli.sys.platform", "darwin"), patch("agent_bridge.cli.macos_notification_capability", return_value=capability), patch("agent_bridge.cli.macos_signing_assessment", return_value=assessment):
                report = execute_command(BridgeService(store), "codex", "doctor", {"strict": True})
        finally:
            store.close()

        self.assertTrue(report["checks"]["native_notifications"])
        self.assertFalse(report["checks"]["native_notification_signing"])
        self.assertEqual(report["notification_capability"]["signing"]["status"], "unsigned")
        self.assertFalse(report["ok"])

    def test_macos_status_capability_exposes_signing_and_gatekeeper_separately(self) -> None:
        helper = self.helper("print('{}')")
        expected = MacOSSigningAssessment("notarized", "signature valid", "notarized")
        with patch.dict(os.environ, {"AGENT_BRIDGE_MACOS_NOTIFY_HELPER": str(helper)}), patch("agent_bridge.notifications.sys.platform", "darwin"), patch("agent_bridge.notifications.MacOSNotifier.status", return_value=type("Result", (), {"ok": True, "detail": "ready"})()), patch("agent_bridge.notifications.macos_signing_assessment", return_value=expected):
            capability = macos_notification_capability()

        self.assertEqual(capability.signing_status, "notarized")
        self.assertEqual(capability.gatekeeper, "notarized")

    def test_signing_assessment_uses_verify_then_fixed_gatekeeper_assessment(self) -> None:
        from agent_bridge.notifications import macos_signing_assessment
        app = self.directory / "AgentBridgeNotifier.app"
        helper = app / "Contents" / "MacOS" / "AgentBridgeNotifier"
        helper.parent.mkdir(parents=True)
        helper.write_text("helper", encoding="utf-8")
        responses = [
            CompletedProcess([], 0, "", ""),
            CompletedProcess([], 0, "Signature=Developer ID Application", ""),
            CompletedProcess([], 0, "accepted\nsource=Notarized Developer ID", ""),
        ]
        with patch("agent_bridge.notifications.sys.platform", "darwin"), patch("agent_bridge.notifications.subprocess.run", side_effect=responses) as run:
            assessment = macos_signing_assessment(helper)

        self.assertEqual(assessment.status, "notarized")
        self.assertEqual(assessment.gatekeeper, "notarized")
        self.assertEqual(run.call_args_list[0].args[0], ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)])
        self.assertEqual(run.call_args_list[1].args[0], ["/usr/bin/codesign", "-dvvv", str(app)])
        self.assertEqual(run.call_args_list[2].args[0], ["/usr/sbin/spctl", "--assess", "--type", "execute", "--verbose=4", str(app)])

    def test_strict_doctor_rejects_gatekeeper_rejection_even_with_a_valid_signature(self) -> None:
        helper = self.helper("print('{}')")
        store = Store.open(self.directory / "gatekeeper.sqlite3")
        capability = type("Capability", (), {"available": True, "helper_path": str(helper), "detail": "ready", "expiry_detail": "no native expiry", "signing_status": "signed", "gatekeeper": "rejected"})()
        assessment = MacOSSigningAssessment("signed", "signature valid", "rejected")
        try:
            with patch("agent_bridge.cli.sys.platform", "darwin"), patch("agent_bridge.cli.macos_notification_capability", return_value=capability), patch("agent_bridge.cli.macos_signing_assessment", return_value=assessment):
                report = execute_command(BridgeService(store), "codex", "doctor", {"strict": True})
        finally:
            store.close()

        self.assertFalse(report["checks"]["native_notification_signing"])
        self.assertFalse(report["ok"])

    def test_static_swift_package_and_release_assets_enforce_the_contract(self) -> None:
        root = Path(__file__).resolve().parents[2] / "native" / "macos-notify"
        source = (root / "Sources" / "AgentBridgeNotifier" / "Protocol.swift").read_text(encoding="utf-8")
        delegate = (root / "Sources" / "AgentBridgeNotifier" / "AppDelegate.swift").read_text(encoding="utf-8")
        package = (root / "Package.swift").read_text(encoding="utf-8")
        plist = (root / "Info.plist").read_text(encoding="utf-8")
        entitlements = (root / "AgentBridgeNotifier.entitlements").read_text(encoding="utf-8")
        build = (root / "scripts" / "build-universal2.sh").read_text(encoding="utf-8")
        sign = (root / "scripts" / "sign-and-notarize.sh").read_text(encoding="utf-8")

        self.assertIn("UserNotifications", delegate)
        self.assertIn("UNNotificationCategory", delegate)
        self.assertIn("view", delegate)
        self.assertIn("claim", delegate)
        self.assertIn("snooze", delegate)
        self.assertIn("UNNotificationRequest", delegate)
        self.assertIn("removePendingNotificationRequests", delegate)
        self.assertIn("removeDeliveredNotifications", delegate)
        self.assertIn("Process()", delegate)
        self.assertIn("arguments =", delegate)
        self.assertIn("--cleanup-expired", delegate)
        self.assertNotIn("/bin/sh", delegate)
        self.assertIn("maxInputBytes", source)
        self.assertIn("Decodable", source)
        self.assertIn("Codable", source)
        self.assertIn("expires_in_seconds", source)
        self.assertIn("activation_argv", source)
        self.assertIn(".macOS(.v12)", package)
        self.assertIn("CFBundleIdentifier", plist)
        self.assertIn("LSUIElement", plist)
        self.assertIn("com.apple.security.app-sandbox", entitlements)
        self.assertIn("x86_64", build)
        self.assertIn("arm64", build)
        self.assertIn("lipo", build)
        self.assertIn("5242880", build)
        self.assertIn("AGENT_BRIDGE_CODESIGN_IDENTITY", sign)
        self.assertIn("AGENT_BRIDGE_NOTARY_PROFILE", sign)
        self.assertNotIn("set -x", sign)


if __name__ == "__main__":
    unittest.main()
