from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_bridge.managed_config import ConcurrentEdit
from agent_bridge.path_ownership import (
    PathBackend,
    PersistentPath,
    ensure_launcher_path,
    path_status,
    remove_launcher_path,
)


class FakePathBackend(PathBackend):
    def __init__(self, *, platform: str, user_path: str = "", current_path: str = "") -> None:
        self.platform = platform
        self._user_path = PersistentPath(user_path, "expand")
        self._current_path = current_path
        self.writes: list[PersistentPath] = []
        self.broadcasts = 0

    @property
    def separator(self) -> str:
        return ";" if self.platform == "windows" else ":"

    def read_user_path(self) -> PersistentPath:
        return self._user_path

    def write_user_path(self, value: PersistentPath) -> None:
        self._user_path = value
        self.writes.append(value)

    def read_current_path(self) -> str:
        return self._current_path

    def write_current_path(self, value: str) -> None:
        self._current_path = value

    def broadcast_environment_change(self) -> None:
        self.broadcasts += 1

    def profile_path(self, home: Path) -> Path:
        return home / ".profile"


class LauncherPathOwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name) / "用户 [path]"
        self.home.mkdir()
        self.launcher = self.home / ".local" / "bin"
        self.launcher.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_windows_adds_only_the_absent_launcher_directory_and_preserves_expand_type(self) -> None:
        backend = FakePathBackend(platform="windows", user_path=r"C:\\Tools", current_path=r"C:\\Tools")

        effect = ensure_launcher_path(self.home, self.launcher, backend=backend)

        self.assertTrue(effect.added)
        self.assertEqual("expand", backend.writes[-1].value_type)
        self.assertEqual([r"C:\\Tools", str(self.launcher)], backend.read_user_path().value.split(";"))
        self.assertEqual(1, backend.broadcasts)
        self.assertEqual(backend.read_user_path().value, backend.read_current_path())

    def test_windows_deduplicates_normalized_entries_without_claiming_them(self) -> None:
        entry = str(self.launcher).replace("/", "\\") + r"\\"
        backend = FakePathBackend(platform="windows", user_path=entry, current_path=entry)

        effect = ensure_launcher_path(self.home, self.launcher, backend=backend)

        self.assertFalse(effect.added)
        self.assertEqual([], backend.writes)
        self.assertEqual(0, backend.broadcasts)
        self.assertFalse(remove_launcher_path(self.home, backend=backend))
        self.assertEqual(entry, backend.read_user_path().value)

    def test_windows_repair_and_uninstall_preserve_concurrent_edits(self) -> None:
        backend = FakePathBackend(platform="windows", user_path=r"C:\\Tools", current_path=r"C:\\Tools")
        ensure_launcher_path(self.home, self.launcher, backend=backend)
        ensure_launcher_path(self.home, self.launcher, backend=backend)
        backend._user_path = PersistentPath(backend.read_user_path().value + r";C:\\Other", "expand")
        backend._current_path = backend._user_path.value

        self.assertTrue(remove_launcher_path(self.home, backend=backend))

        self.assertEqual(r"C:\\Tools;C:\\Other", backend.read_user_path().value)
        self.assertEqual(backend.read_user_path().value, backend.read_current_path())

    def test_uninstall_refuses_a_forged_receipt_without_removing_the_user_path_sentinel(self) -> None:
        sentinel = r"C:\\KeepMe"
        backend = FakePathBackend(platform="windows", user_path=sentinel, current_path=sentinel)
        receipt = self.home / ".agent-bridge" / "launcher-path-receipt.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_text(json.dumps({
            "owner": "agent-bridge", "schema": 1, "entry": sentinel, "added": True,
        }), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "launcher entry"):
            remove_launcher_path(self.home, backend=backend)

        self.assertEqual(sentinel, backend.read_user_path().value)
        self.assertEqual(sentinel, backend.read_current_path())
        self.assertTrue(receipt.exists())

    def test_posix_uses_a_bounded_profile_block_without_clobbering_other_bytes(self) -> None:
        profile = self.home / ".profile"
        original = b"# user setting\r\nexport LANG=C\r\n"
        profile.write_bytes(original)
        backend = FakePathBackend(platform="posix", current_path="/usr/bin")

        effect = ensure_launcher_path(self.home, self.launcher, backend=backend)
        installed = profile.read_bytes()
        self.assertTrue(effect.added)
        self.assertTrue(installed.startswith(original))
        self.assertIn(b"# >>> agent-bridge:launcher-path >>>\r\n", installed)
        self.assertIn(os.fsencode(str(self.launcher)), installed)
        ensure_launcher_path(self.home, self.launcher, backend=backend)
        self.assertEqual(installed, profile.read_bytes())

        self.assertTrue(remove_launcher_path(self.home, backend=backend))
        self.assertEqual(original, profile.read_bytes())

    def test_posix_refuses_a_symlink_profile_and_dry_run_makes_no_changes(self) -> None:
        target = self.home / "actual-profile"
        target.write_text("export OLD=1\n", encoding="utf-8")
        profile = self.home / ".profile"
        try:
            profile.symlink_to(target)
        except (NotImplementedError, OSError):
            self.skipTest("symlinks unavailable")
        backend = FakePathBackend(platform="posix", current_path="/usr/bin")

        with self.assertRaisesRegex(ValueError, "symlink"):
            ensure_launcher_path(self.home, self.launcher, backend=backend)
        self.assertEqual("export OLD=1\n", target.read_text(encoding="utf-8"))
        self.assertEqual("/usr/bin", backend.read_current_path())

    def test_posix_refuses_a_concurrent_profile_edit_without_clobbering_it(self) -> None:
        profile = self.home / ".profile"
        profile.write_bytes(b"export LANG=C\n")
        backend = FakePathBackend(platform="posix", current_path="/usr/bin")
        from agent_bridge import path_ownership

        real_atomic_edit = path_ownership.apply_atomic_edit
        raced = False

        def concurrent_edit(target, edit, **kwargs):
            nonlocal raced
            if target == profile and not raced:
                raced = True
                profile.write_bytes(b"export EXTERNAL=1\n")
            return real_atomic_edit(target, edit, **kwargs)

        with patch("agent_bridge.path_ownership.apply_atomic_edit", side_effect=concurrent_edit):
            with self.assertRaises(ConcurrentEdit):
                ensure_launcher_path(self.home, self.launcher, backend=backend)

        self.assertEqual(b"export EXTERNAL=1\n", profile.read_bytes())
        self.assertEqual("/usr/bin", backend.read_current_path())

    def test_status_degrades_when_launcher_is_not_discoverable(self) -> None:
        backend = FakePathBackend(platform="posix", current_path="/usr/bin")

        status = path_status(self.home, backend=backend)

        self.assertFalse(status["available"])
        self.assertIn("not discoverable", status["degradation"])

    def test_windows_status_requires_launcher_and_both_current_and_persistent_path_entries(self) -> None:
        launcher = self.launcher / "bridge.cmd"
        launcher.write_text("@echo off\n", encoding="utf-8")
        entry = str(self.launcher)
        backend = FakePathBackend(platform="windows", user_path=entry, current_path=entry)

        healthy = path_status(self.home, backend=backend)
        backend._user_path = PersistentPath(r"C:\\Tools", "expand")
        degraded = path_status(self.home, backend=backend)

        self.assertTrue(healthy["available"])
        self.assertTrue(healthy["persistent_path"])
        self.assertTrue(healthy["current_path"])
        self.assertFalse(degraded["available"])
        self.assertIn("persistent PATH", degraded["degradation"])

    def test_posix_status_accepts_an_exact_managed_profile_block_but_degrades_when_removed(self) -> None:
        launcher = self.launcher / "bridge"
        launcher.write_text("#!/bin/sh\n", encoding="utf-8")
        backend = FakePathBackend(platform="posix", current_path="/usr/bin")
        ensure_launcher_path(self.home, self.launcher, backend=backend)
        backend._current_path = "/usr/bin"

        healthy = path_status(self.home, backend=backend)
        (self.home / ".profile").write_bytes(b"# user removed the managed block\n")
        degraded = path_status(self.home, backend=backend)

        self.assertTrue(healthy["available"])
        self.assertTrue(healthy["managed_profile"])
        self.assertFalse(healthy["current_path"])
        self.assertFalse(degraded["available"])
        self.assertIn("managed profile block", degraded["degradation"])


if __name__ == "__main__":
    unittest.main()
