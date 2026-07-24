from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
BRIDGE_PATH = SCRIPTS / "bridge.py"
MCP_PATH = SCRIPTS / "bridge_mcp.py"


def load_bridge():
    module_name = f"bridge_under_test_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, BRIDGE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {BRIDGE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def run_bridge(
    home: Path,
    *args: str,
    output_encoding: str = "utf-8",
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["AGENT_BRIDGE_HOME"] = str(home)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(BRIDGE_PATH), *args],
        capture_output=True,
        text=True,
        encoding=output_encoding,
        errors="strict",
        env=env,
        timeout=30,
    )


def write_agent(
    home: Path,
    name: str,
    *,
    skills: list[str] | None = None,
    strengths: str = "",
    wake_argv: list[str] | None = None,
    last_seen: str | None = None,
) -> Path:
    path = home / "agents" / name / "agent.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    profile: dict[str, object] = {"name": name}
    if skills is not None:
        profile["skills"] = skills
    if strengths:
        profile["strengths"] = strengths
    if wake_argv is not None:
        profile["wake_argv"] = wake_argv
    if last_seen is not None:
        profile["last_seen"] = last_seen
    path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    return path


def read_board(home: Path, project: str = "default") -> dict:
    path = home / "projects" / project / "board.json"
    return json.loads(path.read_text(encoding="utf-8"))
