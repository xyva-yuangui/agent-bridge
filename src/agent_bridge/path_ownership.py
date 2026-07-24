"""Conservative ownership of the Agent Bridge launcher PATH entry.

The durable receipt records only the one entry Agent Bridge added.  It never
stores a snapshot of the user's complete PATH, which lets uninstall coexist
with edits made by shells, installers, and other applications.
"""

from __future__ import annotations

import ctypes
import json
import ntpath
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import MutableMapping, Optional

from .managed_config import OWNER, apply_atomic_edit, content_hash, install_managed_block, remove_managed_block


PATH_RECEIPT_SCHEMA = 1
PATH_BLOCK_NAME = "launcher-path"


@dataclass(frozen=True)
class PersistentPath:
    """A user PATH value and the representation required to write it back."""

    value: str
    value_type: object


@dataclass(frozen=True)
class LauncherPathEffect:
    entry: str
    added: bool
    changed: bool
    target: Path


class PathBackend:
    """Injectable boundary around process, user-environment, and profile IO."""

    platform = "posix"

    @property
    def separator(self) -> str:
        raise NotImplementedError

    def read_user_path(self) -> PersistentPath:
        raise NotImplementedError

    def write_user_path(self, value: PersistentPath) -> None:
        raise NotImplementedError

    def read_current_path(self) -> str:
        raise NotImplementedError

    def write_current_path(self, value: str) -> None:
        raise NotImplementedError

    def broadcast_environment_change(self) -> None:
        """Best-effort notification; failure must not fail a local setup."""

    def profile_path(self, home: Path) -> Path:
        raise NotImplementedError


class WindowsPathBackend(PathBackend):
    platform = "windows"

    def __init__(self, environ: Optional[MutableMapping[str, str]] = None) -> None:
        self._environ = os.environ if environ is None else environ

    @property
    def separator(self) -> str:
        return ";"

    def read_user_path(self) -> PersistentPath:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
                value, value_type = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            return PersistentPath("", winreg.REG_EXPAND_SZ)
        if not isinstance(value, str):
            raise ValueError("HKCU\\Environment Path is not a string")
        # Preserve REG_EXPAND_SZ exactly; it is common for a user PATH to
        # deliberately contain expandable variables such as %USERPROFILE%.
        return PersistentPath(value, value_type)

    def write_user_path(self, value: PersistentPath) -> None:
        import winreg

        value_type = value.value_type
        if value_type not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
            value_type = winreg.REG_EXPAND_SZ
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
            winreg.SetValueEx(key, "Path", 0, value_type, value.value)

    def read_current_path(self) -> str:
        return self._environ.get("PATH", "")

    def write_current_path(self, value: str) -> None:
        self._environ["PATH"] = value

    def broadcast_environment_change(self) -> None:
        try:
            user32 = ctypes.windll.user32
            result = ctypes.c_ulong()
            user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 2000, ctypes.byref(result))
        except (AttributeError, OSError):
            pass

    def profile_path(self, home: Path) -> Path:
        return home / ".profile"


class PosixPathBackend(PathBackend):
    platform = "posix"

    def __init__(self, environ: Optional[MutableMapping[str, str]] = None) -> None:
        self._environ = os.environ if environ is None else environ

    @property
    def separator(self) -> str:
        return ":"

    def read_user_path(self) -> PersistentPath:
        return PersistentPath(self.read_current_path(), "process")

    def write_user_path(self, value: PersistentPath) -> None:
        raise RuntimeError("POSIX user PATH is owned through ~/.profile")

    def read_current_path(self) -> str:
        return self._environ.get("PATH", "")

    def write_current_path(self, value: str) -> None:
        self._environ["PATH"] = value

    def broadcast_environment_change(self) -> None:
        return None

    def profile_path(self, home: Path) -> Path:
        return home / ".profile"


def default_path_backend() -> PathBackend:
    return WindowsPathBackend() if os.name == "nt" else PosixPathBackend()


def launcher_directory(home: Path) -> Path:
    return Path(home).expanduser().absolute() / ".local" / "bin"


def _canonical_launcher_entry(home: Path) -> str:
    """Return the only launcher entry this installation may own."""
    user_home = Path(home).expanduser().absolute()
    local = user_home / ".local"
    launcher = launcher_directory(user_home)
    if launcher.parent != local or local.parent != user_home:
        raise ValueError("launcher directory escapes its home")
    for path in (user_home, local, launcher):
        if path.exists() and path.is_symlink():
            raise ValueError("refusing a symlinked launcher directory")
    return str(launcher)


def _normal_entry(value: str, backend: PathBackend) -> str:
    if backend.platform == "windows":
        return ntpath.normcase(ntpath.normpath(value.rstrip("\\/")))
    return os.path.normpath(value.rstrip("/") or "/")


def _contains(value: str, entry: str, backend: PathBackend) -> bool:
    wanted = _normal_entry(entry, backend)
    return any(_normal_entry(part, backend) == wanted for part in value.split(backend.separator) if part)


def _append(value: str, entry: str, backend: PathBackend) -> str:
    return entry if not value else value + backend.separator + entry


def _remove_one(value: str, entry: str, backend: PathBackend) -> str:
    """Remove only one normalized match, retaining all unrelated order."""
    parts = value.split(backend.separator)
    wanted = _normal_entry(entry, backend)
    for index in range(len(parts) - 1, -1, -1):
        if parts[index] and _normal_entry(parts[index], backend) == wanted:
            del parts[index]
            break
    return backend.separator.join(parts)


def _receipt_path(home: Path) -> Path:
    user_home = Path(home).expanduser().absolute()
    root = user_home / ".agent-bridge"
    if root.exists() and (root.is_symlink() or not root.is_dir()):
        raise ValueError("refusing an unsafe Agent Bridge PATH receipt root")
    receipt = root / "launcher-path-receipt.json"
    if receipt.parent != user_home / ".agent-bridge":
        raise ValueError("PATH receipt escapes its home")
    return receipt


def launcher_path_receipt(home: Path) -> Path:
    """Return the owned receipt location without reading or creating it."""
    return _receipt_path(home)


def has_launcher_path_receipt(home: Path, entry: str) -> bool:
    return _read_receipt(home, entry) is not None


def _read_receipt(home: Path, entry: Optional[str] = None) -> Optional[dict]:
    receipt = _receipt_path(home)
    if not receipt.exists():
        return None
    if receipt.is_symlink() or not receipt.is_file():
        raise ValueError("refusing an unsafe Agent Bridge PATH receipt")
    try:
        value = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("invalid Agent Bridge PATH receipt") from error
    if not isinstance(value, dict) or value.get("owner") != OWNER or value.get("schema") != PATH_RECEIPT_SCHEMA:
        raise ValueError("refusing to replace an unowned PATH receipt")
    if not isinstance(value.get("entry"), str) or not isinstance(value.get("added"), bool):
        raise ValueError("invalid Agent Bridge PATH receipt")
    if entry is not None and value["entry"] != entry:
        raise ValueError("PATH receipt launcher entry does not match this installation")
    return value


def _write_receipt(home: Path, entry: str, added: bool) -> Path:
    receipt = _receipt_path(home)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    if receipt.exists() and (receipt.is_symlink() or not receipt.is_file()):
        raise ValueError("refusing an unsafe Agent Bridge PATH receipt")
    before = receipt.read_bytes() if receipt.exists() else b""
    payload = json.dumps(
        {"owner": OWNER, "schema": PATH_RECEIPT_SCHEMA, "entry": entry, "added": added},
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    apply_atomic_edit(receipt, lambda ignored: payload, expected_hash=content_hash(before))
    return receipt


def _delete_receipt(home: Path) -> None:
    receipt = _receipt_path(home)
    if receipt.exists():
        if receipt.is_symlink() or not receipt.is_file():
            raise ValueError("refusing an unsafe Agent Bridge PATH receipt")
        receipt.unlink()


def _safe_profile(home: Path, backend: PathBackend) -> Path:
    home = Path(home).expanduser().absolute()
    profile = backend.profile_path(home).absolute()
    if home.is_symlink() or profile != home / ".profile":
        raise ValueError("refusing an unsafe profile path")
    if profile.exists() and (profile.is_symlink() or not profile.is_file()):
        raise ValueError("refusing a symlink or non-regular profile")
    return profile


def _profile_payload(entry: str, newline: bytes = b"\n") -> bytes:
    return ("export PATH={0}:\"$PATH\"".format(shlex.quote(entry))).encode("utf-8") + newline


def ensure_launcher_path(home: Path, launcher_dir: Optional[Path] = None, *, backend: Optional[PathBackend] = None) -> LauncherPathEffect:
    """Ensure the launcher is discoverable without claiming an existing entry."""
    backend = default_path_backend() if backend is None else backend
    entry = _canonical_launcher_entry(home)
    if launcher_dir is not None and str(Path(launcher_dir).absolute()) != entry:
        raise ValueError("launcher entry must be the canonical launcher directory")
    prior = _read_receipt(home, entry)
    owned_added = bool(prior and prior["added"])
    changed = False

    if backend.platform == "windows":
        user_path = backend.read_user_path()
        if not _contains(user_path.value, entry, backend):
            backend.write_user_path(PersistentPath(_append(user_path.value, entry, backend), user_path.value_type))
            backend.broadcast_environment_change()
            changed = True
            owned_added = True
        current = backend.read_current_path()
        if not _contains(current, entry, backend):
            backend.write_current_path(_append(current, entry, backend))
            changed = True
        target = _receipt_path(home)
    else:
        profile = _safe_profile(home, backend)
        current = backend.read_current_path()
        should_install_block = owned_added or not _contains(current, entry, backend)
        if should_install_block:
            before = profile.read_bytes() if profile.exists() else b""
            newline = b"\r\n" if b"\r\n" in before else b"\n"
            after = install_managed_block(before, PATH_BLOCK_NAME, _profile_payload(entry, newline))
            if after != before:
                apply_atomic_edit(profile, lambda ignored: after, expected_hash=content_hash(before))
                changed = True
            owned_added = True
        if not _contains(current, entry, backend):
            backend.write_current_path(_append(current, entry, backend))
            changed = True
        target = profile

    _write_receipt(home, entry, owned_added)
    return LauncherPathEffect(entry, owned_added, changed, target)


def remove_launcher_path(home: Path, *, backend: Optional[PathBackend] = None) -> bool:
    """Remove only the exact entry this installation recorded as its own."""
    backend = default_path_backend() if backend is None else backend
    entry = _canonical_launcher_entry(home)
    receipt = _read_receipt(home, entry)
    if receipt is None:
        return False
    if not receipt["added"]:
        _delete_receipt(home)
        return False

    if backend.platform == "windows":
        user_path = backend.read_user_path()
        replacement = _remove_one(user_path.value, entry, backend)
        if replacement != user_path.value:
            backend.write_user_path(PersistentPath(replacement, user_path.value_type))
            backend.broadcast_environment_change()
        current = backend.read_current_path()
        replacement = _remove_one(current, entry, backend)
        if replacement != current:
            backend.write_current_path(replacement)
    else:
        profile = _safe_profile(home, backend)
        before = profile.read_bytes() if profile.exists() else b""
        after = remove_managed_block(before, PATH_BLOCK_NAME)
        if after != before:
            apply_atomic_edit(profile, lambda ignored: after, expected_hash=content_hash(before))
        current = backend.read_current_path()
        replacement = _remove_one(current, entry, backend)
        if replacement != current:
            backend.write_current_path(replacement)
    _delete_receipt(home)
    return True


def _has_exact_managed_profile_block(home: Path, entry: str, backend: PathBackend) -> bool:
    profile = _safe_profile(home, backend)
    if not profile.exists():
        return False
    source = profile.read_bytes()
    newline = b"\r\n" if b"\r\n" in source else b"\n"
    expected = install_managed_block(
        remove_managed_block(source, PATH_BLOCK_NAME), PATH_BLOCK_NAME, _profile_payload(entry, newline),
    )
    return expected == source


def path_status(home: Path, *, backend: Optional[PathBackend] = None) -> dict:
    backend = default_path_backend() if backend is None else backend
    entry = _canonical_launcher_entry(home)
    launcher = Path(entry) / ("bridge.cmd" if backend.platform == "windows" else "bridge")
    launcher_exists = launcher.is_file() and not launcher.is_symlink()
    details = {"entry": entry, "launcher_exists": launcher_exists, "owned": False}
    try:
        receipt = _read_receipt(home, entry)
    except ValueError as error:
        return {"available": False, **details, "degradation": str(error)}
    details["owned"] = bool(receipt and receipt["added"])
    if backend.platform == "windows":
        try:
            persistent = _contains(backend.read_user_path().value, entry, backend)
            current = _contains(backend.read_current_path(), entry, backend)
        except (OSError, ValueError, RuntimeError) as error:
            return {"available": False, **details, "current_path": False, "persistent_path": False, "degradation": str(error)}
        details.update({"current_path": current, "persistent_path": persistent})
        if not launcher_exists:
            degradation = "launcher is not discoverable: launcher file is missing"
        elif not persistent:
            degradation = "launcher is not discoverable: persistent PATH is missing launcher entry"
        elif not current:
            degradation = "launcher is not discoverable: current PATH is missing launcher entry"
        else:
            degradation = None
    else:
        try:
            current = _contains(backend.read_current_path(), entry, backend)
            managed_profile = _has_exact_managed_profile_block(home, entry, backend)
        except (OSError, ValueError, RuntimeError) as error:
            return {"available": False, **details, "current_path": False, "managed_profile": False, "degradation": str(error)}
        details.update({"current_path": current, "managed_profile": managed_profile})
        if not launcher_exists:
            degradation = "launcher is not discoverable: launcher file is missing"
        elif not current and not managed_profile:
            degradation = "launcher is not discoverable: current PATH and managed profile block are missing launcher entry"
        else:
            degradation = None
    return {"available": degradation is None, **details, "degradation": degradation}
