"""Build and verify the offline wheel used by the repository bootstraps.

The installer must work on a fresh Python without setuptools, wheel, or network
access.  This small, stdlib-only verifier makes the tracked wheel auditable:
its version, RECORD entries, package resources, and source inventory must all
match the checkout before release CI accepts it.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "agent_bridge"
BOOTSTRAP = ROOT / "bootstrap"


def version() -> str:
    match = re.search(r'^version = "([^"]+)"$', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.MULTILINE)
    if not match:
        raise RuntimeError("project version is missing")
    return match.group(1)


def wheel_path() -> Path:
    return BOOTSTRAP / "agent_bridge-{}-py3-none-any.whl".format(version())


def metadata_path() -> Path:
    return BOOTSTRAP / "agent_bridge-{}.bootstrap.json".format(version())


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _record_hash(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode("ascii").rstrip("=")


def package_inventory() -> dict[str, str]:
    """The exact package files that must be carried by an offline wheel."""
    allowed_suffixes = {".py", ".sql", ".json", ".ps1", ".exe"}
    inventory: dict[str, str] = {}
    for source in PACKAGE.rglob("*"):
        if not source.is_file() or "__pycache__" in source.parts or source.suffix not in allowed_suffixes:
            continue
        relative = source.relative_to(PACKAGE).as_posix()
        if source.suffix not in {".py", ".sql", ".json", ".ps1", ".exe"}:
            continue
        inventory["agent_bridge/" + relative] = _hash(source.read_bytes())
    return dict(sorted(inventory.items()))


def check() -> dict[str, object]:
    wheel = wheel_path()
    metadata_file = metadata_path()
    if not wheel.is_file() or not metadata_file.is_file():
        raise RuntimeError("offline bootstrap wheel is missing; run scripts/bootstrap_wheel.py --write in a release checkout")
    metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
    actual_wheel_hash = _hash(wheel.read_bytes())
    expected = {
        "schema": 1,
        "version": version(),
        "wheel": wheel.name,
        "sha256": actual_wheel_hash,
        "source_inventory": package_inventory(),
    }
    if metadata != expected:
        raise RuntimeError("bootstrap metadata does not match current wheel/source")
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        wheel_metadata = next(name for name in names if name.endswith(".dist-info/METADATA"))
        wheel_control = next(name for name in names if name.endswith(".dist-info/WHEEL"))
        record_name = next(name for name in names if name.endswith(".dist-info/RECORD"))
        if "Version: {}".format(version()) not in archive.read(wheel_metadata).decode("utf-8"):
            raise RuntimeError("bootstrap wheel version is stale")
        if "Tag: py3-none-any" not in archive.read(wheel_control).decode("utf-8"):
            raise RuntimeError("bootstrap wheel must remain portable")
        records = {row[0]: row[1:] for row in csv.reader(io.StringIO(archive.read(record_name).decode("utf-8")))}
        for name, digest in package_inventory().items():
            if name not in names or _hash(archive.read(name)) != digest:
                raise RuntimeError("bootstrap wheel resource is stale: " + name)
            if records.get(name) != [_record_hash(archive.read(name)), str(len(archive.read(name)))]:
                raise RuntimeError("bootstrap wheel RECORD is invalid: " + name)
    return {"wheel": wheel.name, "sha256": actual_wheel_hash, "files": len(package_inventory())}


def write() -> dict[str, object]:
    BOOTSTRAP.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        environment = os.environ.copy()
        environment["SOURCE_DATE_EPOCH"] = "315532800"
        subprocess.run(
            [sys.executable, "-m", "pip", "wheel", ".", "--no-build-isolation", "--no-deps", "--wheel-dir", temporary],
            cwd=ROOT, env=environment, check=True,
        )
        built = next(Path(temporary).glob("agent_bridge-{}-py3-none-any.whl".format(version())))
        shutil.copy2(built, wheel_path())
    metadata = {
        "schema": 1,
        "version": version(),
        "wheel": wheel_path().name,
        "sha256": _hash(wheel_path().read_bytes()),
        "source_inventory": package_inventory(),
    }
    metadata_path().write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return check()


def main() -> int:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--write", action="store_true")
    arguments = parser.parse_args()
    report = write() if arguments.write else check()
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
