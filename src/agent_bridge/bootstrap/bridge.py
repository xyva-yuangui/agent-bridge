#!/usr/bin/env python3
import sys
from pathlib import Path


runtime = Path(__file__).resolve().parent.parent / "runtime"
if runtime.is_dir():
    sys.path.insert(0, str(runtime))

from agent_bridge.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
