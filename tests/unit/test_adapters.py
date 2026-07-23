from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_bridge.adapters import (
    ADAPTER_TYPES,
    ClaudeAdapter,
    CodexAdapter,
    ReasonixAdapter,
    ZCodeAdapter,
    adapter_for,
)
from agent_bridge.adapters.base import (
    DeliveryStatus,
    HostAdapter,
    HostCapabilities,
    Surface,
    TaskCard,
)


class AdapterContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.home = Path(self.directory.name)
        for adapter_type in ADAPTER_TYPES:
            adapter_type(self.home).config_path.parent.mkdir(parents=True, exist_ok=True)
        self.task = TaskCard("task-123", "Review 路径", "Bounded task context")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_every_adapter_reports_real_capabilities(self) -> None:
        for adapter_type in ADAPTER_TYPES:
            with self.subTest(adapter=adapter_type.__name__):
                adapter = adapter_type(self.home)
                capabilities = adapter.capabilities()
                self.assertIn(
                    capabilities.surface,
                    {Surface.NATIVE_PANEL, Surface.SESSION_CARD, Surface.TERMINAL_FALLBACK},
                )
                self.assertGreaterEqual(capabilities.protocol_version, 1)
                self.assertRegex(capabilities.integration_version, r"^\d+\.\d+\.\d+$")

    def test_missing_host_is_not_reported_as_delivered(self) -> None:
        result = ZCodeAdapter(self.home / "missing").notify_in_app(self.task)

        self.assertEqual(result.status, DeliveryStatus.FAILED)
        self.assertIn("not detected", result.message)

    def test_registry_uses_canonical_names_and_aliases(self) -> None:
        self.assertIsInstance(adapter_for("codex", self.home), CodexAdapter)
        self.assertIsInstance(adapter_for("claude-code", self.home), ClaudeAdapter)
        self.assertIsInstance(adapter_for("reasonix", self.home), ReasonixAdapter)
        self.assertIsInstance(adapter_for("z-code", self.home), ZCodeAdapter)
        with self.assertRaisesRegex(KeyError, "unknown host"):
            adapter_for("unsupported", self.home)

    def test_base_adapter_is_abstract_and_capabilities_are_typed(self) -> None:
        with self.assertRaises(TypeError):
            HostAdapter(self.home)  # type: ignore[abstract]
        with self.assertRaises(ValueError):
            HostCapabilities(Surface.SESSION_CARD, True, False, True, 0, "1.0.0")

    def test_task_card_rejects_unbounded_or_invalid_payloads(self) -> None:
        with self.assertRaisesRegex(ValueError, "task_id"):
            TaskCard("", "subject", "body")
        with self.assertRaisesRegex(ValueError, "body"):
            TaskCard("task", "subject", "x" * 8193)

    def test_present_host_notifies_a_session_card_and_acknowledges_with_versions(self) -> None:
        calls = []

        def acknowledge(**payload):
            calls.append(payload)

        for adapter_type in ADAPTER_TYPES:
            with self.subTest(adapter=adapter_type.__name__):
                adapter = adapter_type(self.home)
                result = adapter.notify_in_app(self.task, acknowledge)
                capabilities = adapter.capabilities()

                self.assertEqual(result.status, DeliveryStatus.PLUGIN_DELIVERED)
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls.pop(), {
                    "host_identity": adapter.name,
                    "task_id": self.task.task_id,
                    "integration_version": capabilities.integration_version,
                    "protocol_version": capabilities.protocol_version,
                })

    def test_adapter_without_ack_capability_does_not_fabricate_an_acknowledgement(self) -> None:
        class ReadOnlyCodex(CodexAdapter):
            def capabilities(self) -> HostCapabilities:
                return HostCapabilities(Surface.SESSION_CARD, False, False, True, 2, "1.0.0")

        adapter = ReadOnlyCodex(self.home)
        calls = []
        result = adapter.notify_in_app(self.task, lambda **payload: calls.append(payload))

        self.assertEqual(result.status, DeliveryStatus.PLUGIN_DELIVERED)
        self.assertEqual(calls, [])

    def test_unavailable_richer_surface_has_terminal_fallback_and_health_warning(self) -> None:
        health = CodexAdapter(self.home / "missing").health_check()

        self.assertFalse(health.ok)
        self.assertEqual(health.capabilities.surface, Surface.TERMINAL_FALLBACK)
        self.assertIn("terminal fallback", health.warning)

    def test_plan_install_is_typed_and_install_is_idempotent(self) -> None:
        adapter = CodexAdapter(self.home)
        plan = adapter.plan_install()
        first = adapter.install(plan)
        second = adapter.install(adapter.plan_install())

        self.assertEqual(plan.host, "codex")
        self.assertTrue(first.ok)
        self.assertTrue(second.ok)
        self.assertEqual(adapter.detect().found, True)
        self.assertEqual(adapter.config_path.read_text(encoding="utf-8").count("agent-bridge:codex"), 2)

    def test_manifests_match_the_runtime_capabilities(self) -> None:
        root = Path(__file__).resolve().parents[2]
        for adapter_type in ADAPTER_TYPES:
            adapter = adapter_type(self.home)
            manifest = json.loads(
                (root / "integrations" / adapter.name / "manifest.json").read_text(encoding="utf-8")
            )
            with self.subTest(adapter=adapter.name):
                self.assertEqual(manifest["host"], adapter.name)
                self.assertEqual(manifest["protocol_version"], adapter.capabilities().protocol_version)
                self.assertEqual(manifest["integration_version"], adapter.capabilities().integration_version)
                self.assertEqual(manifest["acknowledge"]["operation"], "acknowledge")
