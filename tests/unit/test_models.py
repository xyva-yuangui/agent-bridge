import unittest

from agent_bridge.models import AgentProfile, ExecutionPolicy


class ModelTests(unittest.TestCase):
    def test_agent_profile_defaults_to_manual(self):
        profile = AgentProfile(name="zcode")
        self.assertEqual(profile.execution_policy, ExecutionPolicy.MANUAL)
        self.assertEqual(profile.max_concurrency, 1)
