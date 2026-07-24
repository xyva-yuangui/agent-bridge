"""Small cross-platform atomic exchange primitive for compare-and-swap edits."""
from __future__ import annotations
import ctypes
import errno
import os
from pathlib import Path

class ExchangeUnsupported(RuntimeError): pass

def exchange(first: Path, second: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    left, right = os.fsencode(first), os.fsencode(second)
    if os.uname().sysname == "Darwin":
        function = getattr(libc, "renamex_np", None)
        if function is None: raise ExchangeUnsupported("renamex_np unavailable")
        result = function(left, right, 0x2)
    else:
        function = getattr(libc, "renameat2", None)
        if function is None: raise ExchangeUnsupported("renameat2 unavailable")
        result = function(-100, left, -100, right, 0x2)
    if result != 0:
        code = ctypes.get_errno()
        if code in (errno.ENOSYS, errno.EOPNOTSUPP, errno.EXDEV, errno.EINVAL):
            raise ExchangeUnsupported(os.strerror(code))
        raise OSError(code, os.strerror(code))

def fsync_parent(path: Path) -> None:
    try:
        descriptor = os.open(str(path.parent), os.O_RDONLY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
    except OSError: pass
