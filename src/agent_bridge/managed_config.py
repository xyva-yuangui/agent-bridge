"""Byte-preserving, reversible configuration edits owned by Agent Bridge."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


OWNER = "agent-bridge"
MANAGED_CONFIG_VERSION = 2


class ConcurrentEdit(RuntimeError):
    """The target changed between planning and the requested edit."""


@dataclass(frozen=True)
class ManagedMutation:
    """A complete, auditable mutation contract for one filesystem target."""

    target: Path
    owner: str
    version: int
    original_hash: str
    backup_path: Optional[Path]
    validation: str
    inverse: str

    def __post_init__(self) -> None:
        if self.owner != OWNER or self.version < 1:
            raise ValueError("invalid managed mutation ownership")


@dataclass(frozen=True)
class AtomicEditResult:
    target: Path
    original_hash: str
    replacement_hash: str


def content_hash(source: bytes) -> str:
    return hashlib.sha256(source).hexdigest()


def _marker(name: str, payload: bytes, ended_with_newline: bool) -> bytes:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name):
        raise ValueError("managed block name must be a safe identifier")
    newline = b"\r\n" if b"\r\n" in payload else b"\n"
    normalized = payload.replace(b"\r\n", b"\n").replace(b"\n", newline)
    if normalized and not normalized.endswith(newline):
        normalized += newline
    boundary = b"1" if ended_with_newline else b"0"
    return (
        b"# >>> agent-bridge:" + name.encode("ascii") + b" >>>" + newline
        + b"# agent-bridge-boundary:" + boundary + newline
        + normalized
        + b"# <<< agent-bridge:" + name.encode("ascii") + b" <<<" + newline
    )


def _remove_block_with_boundary(source: bytes, name: str) -> bytes:
    encoded = re.escape(name.encode("ascii"))
    pattern = re.compile(
        rb"(?ms)# >>> agent-bridge:" + encoded + rb" >>>\r?\n"
        rb"# agent-bridge-boundary:([01])\r?\n.*?"
        rb"# <<< agent-bridge:" + encoded + rb" <<<\r?\n?"
    )

    # A no-final-newline source receives one separator before the block.  The
    # receipt tells us to remove that separator too; a final-newline source
    # already owns its newline and must keep it byte-for-byte.
    result = source
    while True:
        match = pattern.search(result)
        if match is None:
            return result
        prefix = result[:match.start()]
        if match.group(1) == b"0" and prefix.endswith(b"\n"):
            prefix = prefix[:-1]
        result = prefix + result[match.end():]


def remove_managed_block(source: bytes, name: str) -> bytes:
    """Remove only our named block, leaving every other byte untouched."""
    result = _remove_block_with_boundary(source, name)
    # v1 blocks did not carry a boundary receipt.  Their conventional layout
    # always ended in a newline, so retain that layout while upgrading/removing.
    encoded = re.escape(name.encode("ascii"))
    legacy = re.compile(
        rb"(?ms)^# >>> agent-bridge:" + encoded + rb" >>>\r?\n.*?"
        rb"^# <<< agent-bridge:" + encoded + rb" <<<\r?\n?"
    )
    return legacy.sub(b"", result)


def install_managed_block(source: bytes, name: str, payload: bytes) -> bytes:
    """Idempotently append a versioned owned block without decoding user data."""
    original = remove_managed_block(source, name)
    ended = bool(original) and (original.endswith(b"\n") or original.endswith(b"\r"))
    separator = b"" if not original or ended else b"\n"
    return original + separator + _marker(name, payload, ended)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


@contextmanager
def _exchange_lock(target: Path):
    """Portable fallback when an OS exchange primitive is unavailable.

    It never overwrites an observed racer: writers serialize through a sibling
    lock, then re-check the expected content before replacement.
    """
    lock = target.with_name(target.name + ".agent-bridge.exchange.lock")
    deadline = time.monotonic() + 5
    descriptor = None
    while descriptor is None:
        try: descriptor = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.monotonic() >= deadline: raise ConcurrentEdit("timed out waiting for atomic exchange")
            time.sleep(.01)
    try: yield
    finally:
        os.close(descriptor)
        try: lock.unlink()
        except OSError: pass


def apply_atomic_edit(
    target: Path,
    edit: Callable[[bytes], bytes],
    *,
    expected_hash: Optional[str] = None,
    validate: Optional[Callable[[bytes], bool]] = None,
) -> AtomicEditResult:
    """fsync a sibling temporary file then replace the target atomically.

    ``expected_hash`` is a compare-before-swap guard for a plan prepared
    earlier.  A caller that needs retries can re-read and rebuild its plan.
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    before = target.read_bytes() if target.exists() else b""
    before_hash = content_hash(before)
    if expected_hash is not None and before_hash != expected_hash:
        raise ConcurrentEdit("target changed since setup was planned: {0}".format(target))
    after = edit(before)
    if not isinstance(after, bytes):
        raise TypeError("atomic edits must return bytes")
    if validate is not None and not validate(after):
        raise ValueError("replacement failed validation: {0}".format(target))
    descriptor, temporary_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".agent-bridge.tmp", dir=str(target.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(after)
            stream.flush()
            os.fsync(stream.fileno())
        # The lock is the portable Linux/macOS fallback for unavailable
        # renameat2/renamex_np exchange support; it refuses conflicts instead
        # of an unconditional non-Windows failure or a blind overwrite.
        with _exchange_lock(target):
            current = target.read_bytes() if target.exists() else b""
            if content_hash(current) != before_hash:
                raise ConcurrentEdit("target changed during atomic edit: {0}".format(target))
            os.replace(str(temporary), str(target))
        _fsync_directory(target.parent)
        return AtomicEditResult(target, before_hash, content_hash(after))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def backup_file(target: Path, backup_root: Path) -> Optional[Path]:
    """Create an fsynced backup of an existing file for diagnostic recovery."""
    target = Path(target)
    if not target.exists():
        return None
    if target.is_symlink() or not target.is_file():
        raise ValueError("refusing to back up a non-regular target")
    backup_root.mkdir(parents=True, exist_ok=True)
    digest = content_hash(target.read_bytes())[:16]
    destination = backup_root / (target.name + "." + digest + ".bak")
    if destination.exists():
        return destination
    apply_atomic_edit(destination, lambda ignored: target.read_bytes(), expected_hash=content_hash(b""))
    return destination
