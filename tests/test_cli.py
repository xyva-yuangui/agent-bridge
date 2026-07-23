from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support import BRIDGE_PATH


class CliEncodingTests(unittest.TestCase):
    def test_status_does_not_crash_with_gbk_stdio(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env = os.environ.copy()
            env["AGENT_BRIDGE_HOME"] = temp_dir
            env["PYTHONIOENCODING"] = "gbk"
            env.pop("PYTHONUTF8", None)

            result = subprocess.run(
                [
                    sys.executable,
                    str(BRIDGE_PATH),
                    "--as",
                    "codex",
                    "status",
                    "--oneliner",
                ],
                capture_output=True,
                text=True,
                encoding="gbk",
                errors="strict",
                env=env,
                timeout=30,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("UnicodeEncodeError", result.stderr)
        self.assertIn("agent-bridge", result.stdout)


if __name__ == "__main__":
    unittest.main()
