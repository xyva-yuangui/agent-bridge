import unittest
from pathlib import Path
from unittest import mock

from agent_bridge.paths import get_data_root, require_local_data_root


class PathTests(unittest.TestCase):
    def test_override_is_resolved_without_requiring_existence(self):
        root = get_data_root({"AGENT_BRIDGE_HOME": "./local-data"})
        self.assertTrue(root.is_absolute())
        self.assertEqual(root.name, "local-data")

    def test_unc_path_is_rejected_on_windows(self):
        with mock.patch("agent_bridge.paths.os.name", "nt"):
            with self.assertRaisesRegex(ValueError, "local filesystem"):
                require_local_data_root(Path(r"\\server\share\bridge"))
