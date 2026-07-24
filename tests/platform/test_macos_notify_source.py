from __future__ import annotations

import unittest
from pathlib import Path


class MacOSNotifySourceTests(unittest.TestCase):
    def test_app_bundle_uses_a_bounded_accessory_lifecycle_for_cold_notification_actions(self) -> None:
        root = Path(__file__).resolve().parents[2] / "native" / "macos-notify" / "Sources" / "AgentBridgeNotifier"
        main = (root / "main.swift").read_text(encoding="utf-8")
        delegate = (root / "AppDelegate.swift").read_text(encoding="utf-8")

        self.assertIn("NSApplication", main)
        self.assertIn("NSApplicationDelegate", main)
        self.assertIn("setActivationPolicy(.accessory)", main)
        self.assertIn("applicationDidFinishLaunching", main)
        self.assertIn("onActionHandled", main)
        self.assertNotIn("protocolInput", main)
        self.assertIn('arguments == [CommandLine.arguments[0], "--protocol"]', main)
        self.assertNotIn("poll(", main)
        self.assertIn("UNUserNotificationCenterDelegate", delegate)
        self.assertIn("didReceive", delegate)
        self.assertIn("onActionHandled", delegate)
        self.assertIn("--activation-uri", delegate)
        self.assertIn("agent-bridge://action/", delegate)

    def test_expiry_cleanup_removes_pending_and_delivered_requests_and_never_claims_native_expiration(self) -> None:
        root = Path(__file__).resolve().parents[2] / "native" / "macos-notify" / "Sources" / "AgentBridgeNotifier"
        delegate = (root / "AppDelegate.swift").read_text(encoding="utf-8")

        self.assertIn("removePendingNotificationRequests", delegate)
        self.assertIn("removeDeliveredNotifications", delegate)
        self.assertIn("cleanupExpiredNotifications", delegate)
        self.assertIn("scheduleExpiryCleanup", delegate)
        self.assertIn("macOS has no native expiration", delegate)
        self.assertIn("opaqueID(arguments[2]", (root / "main.swift").read_text(encoding="utf-8"))
        self.assertIn("ExpiryRecord", delegate)
        self.assertIn("expectedGeneration", delegate)
        self.assertIn("record.generation == expectedGeneration", delegate)


if __name__ == "__main__":
    unittest.main()
