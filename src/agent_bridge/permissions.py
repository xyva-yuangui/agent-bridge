"""Best-effort, fail-closed ownership permissions for local bridge state."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_SECURED_DIRECTORIES = set()
_SECURED_FILES = set()


def secure_directory(path: Path) -> None:
    """Create a directory private to the current OS user.

    POSIX permissions are exact.  Windows uses the built-in ACL tool with a
    separate argv and no shell so database sidecars inherit the same DACL.
    """
    path = path.resolve(strict=False)
    path.mkdir(parents=True, exist_ok=True)
    key = str(path)
    if key in _SECURED_DIRECTORIES:
        return
    if os.name != "nt":
        path.chmod(0o700)
        _SECURED_DIRECTORIES.add(key)
        return
    account = "\\".join(
        value for value in (os.environ.get("USERDOMAIN"), os.environ.get("USERNAME"))
        if value
    )
    if not account:
        raise RuntimeError("cannot determine the current Windows account for data ACL")
    _secure_windows_acl(
        path,
        account + ":(OI)(CI)F",
        "*S-1-5-18:(OI)(CI)F",
    )
    _SECURED_DIRECTORIES.add(key)


def _secure_windows_acl(path: Path, user_grant: str, system_grant: str) -> None:
    result = subprocess.run(
        ["icacls.exe", str(path), "/inheritance:r", "/grant:r", user_grant, "/grant:r", system_grant],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError("failed to restrict Agent Bridge data ACL: " + result.stderr.strip())


def secure_file(path: Path) -> None:
    """Restrict an existing file, including state created before an upgrade."""
    if os.name != "nt":
        path.chmod(0o600)
        return
    path = path.resolve(strict=False)
    key = str(path)
    if key in _SECURED_FILES:
        return
    account = "\\".join(
        value for value in (os.environ.get("USERDOMAIN"), os.environ.get("USERNAME"))
        if value
    )
    if not account:
        raise RuntimeError("cannot determine the current Windows account for data ACL")
    _secure_windows_acl(path, account + ":F", "*S-1-5-18:F")
    _SECURED_FILES.add(key)
