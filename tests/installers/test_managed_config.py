from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_bridge.managed_config import apply_atomic_edit, install_managed_block, remove_managed_block


class ManagedConfigTests(unittest.TestCase):
    def test_install_and_uninstall_preserve_unrelated_bytes(self) -> None:
        original = b"# user comment\r\nkey = '\xe4\xb8\xad'\r\n\xff\n"
        payload = b"answer = 42\n"
        installed = install_managed_block(original, "codex", payload)
        self.assertEqual(installed, install_managed_block(installed, "codex", payload))
        self.assertEqual(original, remove_managed_block(installed, "codex"))

    def test_atomic_edit_handles_a_path_with_spaces_cjk_and_brackets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "用户 [one]" / "config.json"
            apply_atomic_edit(path, lambda source: source + b'{"managed":true}\n')
            self.assertEqual(path.read_bytes(), b'{"managed":true}\n')


if __name__ == "__main__":
    unittest.main()
