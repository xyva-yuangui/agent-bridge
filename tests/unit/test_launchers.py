from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from agent_bridge.launchers import LaunchPolicyError, evaluate_launch, launch_agent
from agent_bridge.models import AgentProfile, ExecutionPolicy


class LauncherPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self.directory.name) / "allowed"
        self.workspace.mkdir()
        self.other = Path(self.directory.name) / "other"
        self.other.mkdir()

    def tearDown(self) -> None:
        self.directory.cleanup()

    def profile(self, **changes):
        values = {
            "name": "zcode",
            "execution_policy": ExecutionPolicy.AUTO,
            "launch_argv": ("agent.exe", "resume"),
            "workspace_allowlist": (str(self.workspace),),
        }
        values.update(changes)
        return AgentProfile(**values)

    def test_sender_cannot_override_manual_policy(self) -> None:
        profile = self.profile(execution_policy=ExecutionPolicy.MANUAL)

        decision = evaluate_launch(profile, self.workspace, 0, None, requested_auto=True)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "target policy is manual")

    def test_workspace_outside_allowlist_is_rejected(self) -> None:
        with self.assertRaisesRegex(LaunchPolicyError, "allowlist"):
            evaluate_launch(self.profile(), self.other, 0, None, requested_auto=True)

    def test_rejects_empty_or_shell_like_configured_argv(self) -> None:
        for argv in ((), ("agent.exe", ""), ("agent.exe", "resume; rm")):
            with self.subTest(argv=argv):
                with self.assertRaisesRegex(LaunchPolicyError, "argv"):
                    evaluate_launch(self.profile(launch_argv=argv), self.workspace, 0, None, requested_auto=True)

    def test_rejects_concurrency_and_cooldown_before_launch(self) -> None:
        profile = self.profile(max_concurrency=1, cooldown_seconds=30)
        concurrent = evaluate_launch(profile, self.workspace, 1, None, requested_auto=True)
        cooling = evaluate_launch(
            profile,
            self.workspace,
            0,
            datetime.now(timezone.utc) - timedelta(seconds=5),
            requested_auto=True,
        )

        self.assertEqual(concurrent.reason, "target concurrency limit reached")
        self.assertEqual(cooling.reason, "target cooldown is active")

    def test_launch_uses_argv_cwd_minimal_environment_and_detachment(self) -> None:
        decision = evaluate_launch(self.profile(), self.workspace, 0, None, requested_auto=True)
        seen = {}

        class Process:
            pid = 42

        def fake_popen(argv, **kwargs):
            seen["argv"] = argv
            seen["kwargs"] = kwargs
            return Process()

        with patch("agent_bridge.launchers.subprocess.Popen", side_effect=fake_popen):
            result = launch_agent(decision)

        self.assertTrue(result.started)
        self.assertEqual(result.pid, 42)
        self.assertEqual(seen["argv"], ["agent.exe", "resume"])
        self.assertEqual(seen["kwargs"]["cwd"], str(self.workspace.resolve()))
        self.assertFalse(seen["kwargs"]["shell"])
        self.assertNotIn("UNRELATED_SECRET", seen["kwargs"]["env"])
        self.assertIs(seen["kwargs"]["stdin"], __import__("subprocess").DEVNULL)
        if os.name == "nt":
            self.assertIn("creationflags", seen["kwargs"])
        else:
            self.assertTrue(seen["kwargs"]["start_new_session"])
