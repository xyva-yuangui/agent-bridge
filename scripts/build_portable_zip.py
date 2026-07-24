"""Create the deterministic, cross-platform Agent Bridge portable release ZIP."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import stat
import time
from typing import Optional, Set
import zipfile


EPOCH = 315532800  # 1980-01-01: the first timestamp representable by ZIP.


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _timestamp() -> tuple[int, int, int, int, int, int]:
    return time.gmtime(EPOCH)[:6]


def _add_file(payload: dict[str, bytes], target: str, source: Path) -> None:
    if not source.is_file():
        raise ValueError("missing portable release input: " + str(source))
    if target in payload:
        raise ValueError("duplicate portable release path: " + target)
    payload[target] = source.read_bytes()


def _add_tree(payload: dict[str, bytes], target_prefix: str, source: Path, suffixes: Optional[Set[str]] = None) -> None:
    if not source.is_dir():
        raise ValueError("missing portable release directory: " + str(source))
    for child in sorted(path for path in source.rglob("*") if path.is_file()):
        if "__pycache__" in child.parts or (suffixes is not None and child.suffix not in suffixes):
            continue
        _add_file(payload, target_prefix + child.relative_to(source).as_posix(), child)


def payload_for(
    source_root: Path, version: str, windows_helper: Path, macos_app: Path,
    bootstrap_root: Optional[Path] = None,
) -> dict[str, bytes]:
    prefix = "agent-bridge-{}/".format(version)
    payload: dict[str, bytes] = {}
    for name in ("install.ps1", "install.sh", "LICENSE", "README.md", "README.zh-CN.md"):
        _add_file(payload, prefix + name, source_root / name)
    for name in ("windows.md", "macos.md", "migration-v1.md"):
        _add_file(payload, prefix + "docs/installation/" + name, source_root / "docs" / "installation" / name)
    # The release pipeline rebuilds this wheel after the Windows helper has
    # been Authenticode-signed.  Do not silently substitute the checkout's
    # bootstrap wheel, or the installed helper could differ from the signed
    # helper visible at the top level of the portable ZIP.
    _add_tree(payload, prefix + "bootstrap/", bootstrap_root or source_root / "bootstrap", {".whl", ".json"})
    _add_tree(payload, prefix + "integrations/", source_root / "src" / "agent_bridge" / "integrations", {".json", ".py"})
    _add_file(payload, prefix + "native/windows-x86_64/agent-bridge-windows-notify.exe", windows_helper)
    _add_tree(payload, prefix + "native/macos-universal2/AgentBridgeNotifier.app/", macos_app)
    return dict(sorted(payload.items()))


def _inventory(payload: dict[str, bytes]) -> tuple[bytes, bytes]:
    files = [{"path": name, "sha256": _sha256(data), "bytes": len(data)} for name, data in sorted(payload.items())]
    inventory = json.dumps({"schema": 1, "files": files}, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    checksums = "".join("{}  {}\n".format(item["sha256"], item["path"]) for item in files).encode("utf-8")
    return inventory, checksums


def build(
    output: Path, source_root: Path, version: str, windows_helper: Path, macos_app: Path,
    bootstrap_root: Optional[Path] = None,
) -> None:
    payload = payload_for(source_root, version, windows_helper, macos_app, bootstrap_root)
    prefix = "agent-bridge-{}/".format(version)
    inventory, checksums = _inventory(payload)
    payload[prefix + "inventory.json"] = inventory
    payload[prefix + "SHA256SUMS.txt"] = checksums
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9, strict_timestamps=True) as archive:
        for name, data in sorted(payload.items()):
            info = zipfile.ZipInfo(name, date_time=_timestamp())
            info.create_system = 3
            executable = name.endswith(".sh") or name.endswith(".exe") or "/AgentBridgeNotifier.app/Contents/MacOS/" in name
            info.external_attr = ((stat.S_IFREG | (0o755 if executable else 0o644)) << 16)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def check(archive_path: Path, version: str) -> None:
    prefix = "agent-bridge-{}/".format(version)
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if names != sorted(names) or any(not name.startswith(prefix) for name in names):
            raise ValueError("portable ZIP paths are not deterministic")
        if any(name.endswith(".zip") and not name.endswith(".whl") for name in names):
            raise ValueError("portable ZIP contains a nested opaque archive")
        inventory_name = prefix + "inventory.json"
        checksum_name = prefix + "SHA256SUMS.txt"
        inventory = json.loads(archive.read(inventory_name))
        checksums = archive.read(checksum_name).decode("utf-8").splitlines()
        expected = []
        for item in inventory["files"]:
            data = archive.read(item["path"])
            if _sha256(data) != item["sha256"] or len(data) != item["bytes"]:
                raise ValueError("portable ZIP inventory mismatch: " + item["path"])
            expected.append("{}  {}".format(item["sha256"], item["path"]))
        if checksums != expected:
            raise ValueError("portable ZIP checksums do not match inventory")
        wheels = [
            name for name in names
            if name.startswith(prefix + "bootstrap/") and name.endswith(".whl")
        ]
        if len(wheels) != 1:
            raise ValueError("portable ZIP must contain exactly one offline bootstrap wheel")
        helper_name = prefix + "native/windows-x86_64/agent-bridge-windows-notify.exe"
        with zipfile.ZipFile(io.BytesIO(archive.read(wheels[0]))) as wheel:
            packaged_helper = "agent_bridge/native/windows-x86_64/agent-bridge-windows-notify.exe"
            if packaged_helper not in wheel.namelist():
                raise ValueError("bootstrap wheel is missing the Windows helper")
            if wheel.read(packaged_helper) != archive.read(helper_name):
                raise ValueError("bootstrap wheel Windows helper differs from the signed ZIP helper")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--version", required=True)
    parser.add_argument("--windows-helper", type=Path)
    parser.add_argument("--macos-app", type=Path)
    parser.add_argument("--bootstrap-root", type=Path, help="signed bootstrap wheel directory from the Windows release job")
    parser.add_argument("--check", type=Path)
    arguments = parser.parse_args()
    if arguments.check:
        check(arguments.check, arguments.version)
    else:
        if not (arguments.output and arguments.windows_helper and arguments.macos_app):
            parser.error("--output, --windows-helper, and --macos-app are required when building")
        build(arguments.output, arguments.source_root, arguments.version, arguments.windows_helper, arguments.macos_app, arguments.bootstrap_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
