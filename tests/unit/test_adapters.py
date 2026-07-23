from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_bridge.adapters import ADAPTER_TYPES, ClaudeAdapter, CodexAdapter, ReasonixAdapter, ZCodeAdapter, adapter_for
from agent_bridge.adapters.base import (
    DeliveryStatus,
    HostAdapter,
    HostCapabilities,
    Surface,
    TaskAcknowledgement,
    TaskCard,
)


class AdapterContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.home = Path(self.directory.name)
        self.task = TaskCard("task-123", "Review 路径", "Bounded task context")
        for adapter_type in ADAPTER_TYPES:
            self._provision(adapter_type(self.home))

    def tearDown(self) -> None:
        self.directory.cleanup()

    def _provision(self, adapter: HostAdapter) -> None:
        adapter.marker_path.parent.mkdir(parents=True, exist_ok=True)
        adapter.marker_path.write_text(
            json.dumps({"host": adapter.name, "mechanisms": [adapter.mechanism]}), encoding="utf-8"
        )

    def _installed(self, adapter: HostAdapter) -> HostAdapter:
        self.assertTrue(adapter.install().ok)
        return adapter

    def test_every_adapter_reports_real_capabilities(self) -> None:
        for adapter_type in ADAPTER_TYPES:
            with self.subTest(adapter=adapter_type.__name__):
                capabilities = adapter_type(self.home).capabilities()
                self.assertIn(capabilities.surface, {Surface.NATIVE_PANEL, Surface.SESSION_CARD, Surface.TERMINAL_FALLBACK})
                self.assertGreaterEqual(capabilities.protocol_version, 1)
                self.assertRegex(capabilities.integration_version, r"^\d+\.\d+\.\d+$")

    def test_capabilities_reject_bool_for_protocol_and_invalid_runtime_types(self) -> None:
        with self.assertRaisesRegex(TypeError, "protocol_version"):
            HostCapabilities(Surface.SESSION_CARD, True, False, True, True, "1.0.0")
        with self.assertRaisesRegex(TypeError, "surface"):
            HostCapabilities("session_card", True, False, True, 2, "1.0.0")  # type: ignore[arg-type]
        with self.assertRaisesRegex(TypeError, "can_ack"):
            HostCapabilities(Surface.SESSION_CARD, 1, False, True, 2, "1.0.0")  # type: ignore[arg-type]

    def test_base_adapter_is_abstract(self) -> None:
        with self.assertRaises(TypeError):
            HostAdapter(self.home)  # type: ignore[abstract]

    def test_registry_uses_canonical_names_and_aliases(self) -> None:
        self.assertIsInstance(adapter_for("codex", self.home), CodexAdapter)
        self.assertIsInstance(adapter_for("claude-code", self.home), ClaudeAdapter)
        self.assertIsInstance(adapter_for("reasonix", self.home), ReasonixAdapter)
        self.assertIsInstance(adapter_for("z-code", self.home), ZCodeAdapter)
        with self.assertRaisesRegex(KeyError, "unknown host"):
            adapter_for("unsupported", self.home)

    def test_empty_or_stale_host_directory_is_not_detected(self) -> None:
        stale = CodexAdapter(self.home / "stale")
        stale.config_path.parent.mkdir(parents=True)

        self.assertFalse(stale.detect().found)
        self.assertFalse(stale.install().ok)
        self.assertEqual(stale.notify_in_app(self.task).status, DeliveryStatus.FAILED)

    def test_missing_host_is_not_reported_as_delivered(self) -> None:
        result = ZCodeAdapter(self.home / "missing").notify_in_app(self.task)

        self.assertEqual(result.status, DeliveryStatus.FAILED)
        self.assertIn("not detected", result.message)

    def test_notify_writes_atomic_session_card_but_never_acknowledges_locally(self) -> None:
        for adapter_type in ADAPTER_TYPES:
            with self.subTest(adapter=adapter_type.__name__):
                adapter = self._installed(adapter_type(self.home))
                result = adapter.notify_in_app(self.task)

                self.assertEqual(result.status, DeliveryStatus.QUEUED)
                self.assertFalse(result.acknowledged)
                self.assertTrue(adapter.task_card_path(self.task.task_id).is_file())
                self.assertEqual(list(adapter.inbox_path.glob("*.tmp")), [])
                card = json.loads(adapter.task_card_path(self.task.task_id).read_text(encoding="utf-8"))
                self.assertEqual(card["host_identity"], adapter.name)
                self.assertEqual(card["task_id"], self.task.task_id)

    def test_explicit_integration_acknowledges_only_after_consuming_queued_card(self) -> None:
        calls = []
        for adapter_type in ADAPTER_TYPES:
            with self.subTest(adapter=adapter_type.__name__):
                adapter = self._installed(adapter_type(self.home))
                adapter.notify_in_app(self.task)
                card = json.loads(adapter.task_card_path(self.task.task_id).read_text(encoding="utf-8"))
                acknowledgement = TaskAcknowledgement(
                    adapter.name,
                    self.task.task_id,
                    card["integration_version"],
                    card["protocol_version"],
                    card["delivery_token"],
                )
                result = adapter.acknowledge_integration(acknowledgement, lambda **payload: calls.append(payload))

                self.assertEqual(result.status, DeliveryStatus.AGENT_ACKNOWLEDGED)
                self.assertTrue(result.acknowledged)
                self.assertEqual(calls.pop(), acknowledgement.as_shared_payload())

    def test_forged_or_mismatched_ack_is_rejected_without_calling_shared_acknowledge(self) -> None:
        adapter = self._installed(CodexAdapter(self.home))
        adapter.notify_in_app(self.task)
        card = json.loads(adapter.task_card_path(self.task.task_id).read_text(encoding="utf-8"))
        calls = []
        forged = TaskAcknowledgement("claude", self.task.task_id, "1.0.0", 2, card["delivery_token"])
        mismatched = TaskAcknowledgement("codex", self.task.task_id, "9.9.9", 2, card["delivery_token"])

        self.assertEqual(adapter.acknowledge_integration(forged, lambda **payload: calls.append(payload)).status, DeliveryStatus.FAILED)
        self.assertEqual(adapter.acknowledge_integration(mismatched, lambda **payload: calls.append(payload)).status, DeliveryStatus.FAILED)
        self.assertEqual(calls, [])

    def test_forged_card_versions_cannot_be_promoted_to_a_shared_acknowledgement(self) -> None:
        adapter = self._installed(CodexAdapter(self.home))
        adapter.notify_in_app(self.task)
        path = adapter.task_card_path(self.task.task_id)
        card = json.loads(path.read_text(encoding="utf-8"))
        card["integration_version"] = "9.9.9"
        path.write_text(json.dumps(card), encoding="utf-8")
        calls = []
        forged = TaskAcknowledgement("codex", self.task.task_id, "9.9.9", 2, card["delivery_token"])

        result = adapter.acknowledge_integration(forged, lambda **payload: calls.append(payload))

        self.assertEqual(result.status, DeliveryStatus.FAILED)
        self.assertEqual(calls, [])

    def test_missing_installed_consumer_fails_without_queuing_a_card(self) -> None:
        adapter = CodexAdapter(self.home)

        result = adapter.notify_in_app(self.task)

        self.assertEqual(result.status, DeliveryStatus.FAILED)
        self.assertFalse(adapter.task_card_path(self.task.task_id).exists())

    def test_task_card_rejects_unbounded_or_invalid_payloads(self) -> None:
        with self.assertRaisesRegex(ValueError, "task_id"):
            TaskCard("", "subject", "body")
        with self.assertRaisesRegex(ValueError, "body"):
            TaskCard("task", "subject", "x" * 8193)

    def test_uninstall_removes_only_its_pending_cards_and_rejects_traversal_ids(self) -> None:
        adapter = self._installed(CodexAdapter(self.home))
        adapter.notify_in_app(self.task)
        sibling = adapter.inbox_path.parent / "claude" / "keep.json"
        sibling.parent.mkdir(parents=True)
        sibling.write_text("keep", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "safe"):
            adapter.task_card_path("../escape")
        adapter.uninstall()

        self.assertFalse(adapter.task_card_path(self.task.task_id).exists())
        self.assertTrue(sibling.exists())

    def test_unavailable_richer_surface_has_terminal_fallback_and_health_warning(self) -> None:
        health = CodexAdapter(self.home / "missing").health_check()

        self.assertFalse(health.ok)
        self.assertEqual(health.capabilities.surface, Surface.TERMINAL_FALLBACK)
        self.assertIn("terminal fallback", health.warning)

    def test_manifests_name_a_host_consumer_entrypoint_and_ack_protocol(self) -> None:
        root = Path(__file__).resolve().parents[2]
        for adapter_type in ADAPTER_TYPES:
            adapter = adapter_type(self.home)
            manifest = json.loads((root / "integrations" / adapter.name / "manifest.json").read_text(encoding="utf-8"))
            with self.subTest(adapter=adapter.name):
                self.assertEqual(manifest["host"], adapter.name)
                self.assertEqual(manifest["protocol_version"], adapter.capabilities().protocol_version)
                self.assertEqual(manifest["integration_version"], adapter.capabilities().integration_version)
                self.assertEqual(manifest["acknowledge"]["operation"], "acknowledge")
                self.assertIn("entrypoint", manifest)
                self.assertIn("session_card", manifest)
