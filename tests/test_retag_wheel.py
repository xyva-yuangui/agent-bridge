from __future__ import annotations

import base64
import csv
import hashlib
import importlib.util
import io
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("retag_wheel", ROOT / "scripts" / "retag_wheel.py")
assert spec and spec.loader
retag_wheel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retag_wheel)


class RetagWheelTests(unittest.TestCase):
    def test_retag_rewrites_wheel_and_complete_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "agent_bridge-2.0.0-py3-none-any.whl"
            destination = Path(temporary) / "agent_bridge-2.0.0-py3-none-win_amd64.whl"
            repeat = Path(temporary) / "repeat.whl"
            with zipfile.ZipFile(source, "w") as wheel:
                wheel.writestr("agent_bridge/__init__.py", "VERSION = '2.0.0'\n")
                wheel.writestr("agent_bridge-2.0.0.dist-info/WHEEL", "Wheel-Version: 1.0\nTag: py3-none-any\n")
                wheel.writestr("agent_bridge-2.0.0.dist-info/RECORD", "")
            retag_wheel.retag(source, destination, "py3-none-win_amd64")
            retag_wheel.retag(source, repeat, "py3-none-win_amd64")
            self.assertEqual(hashlib.sha256(destination.read_bytes()).hexdigest(), hashlib.sha256(repeat.read_bytes()).hexdigest())
            with zipfile.ZipFile(destination) as wheel:
                self.assertIn("Tag: py3-none-win_amd64", wheel.read("agent_bridge-2.0.0.dist-info/WHEEL").decode())
                rows = list(csv.reader(io.StringIO(wheel.read("agent_bridge-2.0.0.dist-info/RECORD").decode())))
                names = {row[0] for row in rows}
                self.assertEqual({"agent_bridge/__init__.py", "agent_bridge-2.0.0.dist-info/WHEEL", "agent_bridge-2.0.0.dist-info/RECORD"}, names)
                init = wheel.read("agent_bridge/__init__.py")
                expected = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(init).digest()).decode().rstrip("=")
                self.assertIn(["agent_bridge/__init__.py", expected, str(len(init))], rows)


if __name__ == "__main__":
    unittest.main()
