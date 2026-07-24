"""Deterministically retag a wheel after staging platform-native payloads.

The project has no compiled Python extensions; platform tags state which native
notification helper was bundled.  This stdlib-only tool rewrites WHEEL and
RECORD without relying on a host-specific wheel utility.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
from pathlib import Path
import zipfile


def _record_hash(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode("ascii").rstrip("=")


def retag(source: Path, destination: Path, tag: str) -> None:
    with zipfile.ZipFile(source) as archive:
        files = {entry.filename: archive.read(entry) for entry in archive.infolist() if not entry.filename.endswith("/RECORD")}
    wheel_name = next(name for name in files if name.endswith(".dist-info/WHEEL"))
    record_name = wheel_name.rsplit("/", 1)[0] + "/RECORD"
    lines = [line for line in files[wheel_name].decode("utf-8").splitlines() if not line.startswith("Tag: ")]
    lines.append("Tag: " + tag)
    files[wheel_name] = ("\n".join(lines) + "\n").encode("utf-8")
    rows = [[name, _record_hash(data), str(len(data))] for name, data in sorted(files.items())]
    rows.append([record_name, "", ""])
    import io
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerows(rows)
    files[record_name] = output.getvalue().encode("utf-8")
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            archive.writestr(name, files[name])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--tag", required=True)
    arguments = parser.parse_args()
    retag(arguments.wheel, arguments.output, arguments.tag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
