from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_bridge import permissions


class LocalPermissionTests(unittest.TestCase):
    def setUp(self) -> None:
        permissions._SECURED_DIRECTORIES.clear()
        permissions._SECURED_FILES.clear()

    def test_data_directory_is_private_to_the_current_user(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "private"
            if os.name == "nt":
                completed = type("Completed", (), {"returncode": 0, "stderr": ""})()
                environment = {"USERDOMAIN": "DOMAIN", "USERNAME": "user"}
                with patch.dict(os.environ, environment, clear=False), patch(
                    "agent_bridge.permissions.subprocess.run",
                    return_value=completed,
                ) as invoked:
                    permissions.secure_directory(target)
                    value = target / "state.json"
                    value.write_text("{}", encoding="utf-8")
                    permissions.secure_file(value)
                directory_argv = invoked.call_args_list[0].args[0]
                file_argv = invoked.call_args_list[1].args[0]
                self.assertEqual("icacls.exe", directory_argv[0])
                self.assertIn("/inheritance:r", directory_argv)
                self.assertIn("DOMAIN\\user:(OI)(CI)F", directory_argv)
                self.assertIn("*S-1-5-18:(OI)(CI)F", directory_argv)
                self.assertIn("DOMAIN\\user:F", file_argv)
                self.assertIn("*S-1-5-18:F", file_argv)
                self.assertTrue(
                    all(not call.kwargs["shell"] for call in invoked.call_args_list)
                )
            else:
                permissions.secure_directory(target)
                self.assertEqual(
                    stat.S_IMODE(target.stat().st_mode),
                    0o700,
                )
                value = target / "state.json"
                value.write_text("{}", encoding="utf-8")
                permissions.secure_file(value)
                self.assertEqual(stat.S_IMODE(value.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
