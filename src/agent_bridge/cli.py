"""Command-line entry point for Agent Bridge v2."""

import argparse
from typing import Optional, Sequence

from .version import BRIDGE_VERSION


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="bridge")
    parser.add_argument("--version", action="version", version=BRIDGE_VERSION)
    parser.parse_args(argv)
    return 0
