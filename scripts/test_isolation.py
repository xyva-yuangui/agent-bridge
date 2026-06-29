#!/usr/bin/env python3
"""Self-check: project isolation. Two workspaces; agents can only collaborate
when their cwd is inside the same registered workspace."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

BRIDGE = str(Path(__file__).with_name("bridge.py"))


def run(cwd, name, *args, home=None):
    env = dict(os.environ, AGENT_BRIDGE_HOME=home, AGENT_BRIDGE_NAME=name)
    r = subprocess.run([sys.executable, BRIDGE, *args], cwd=cwd, env=env,
                       capture_output=True, text=True, timeout=30)
    return r.returncode, (r.stdout + r.stderr).strip()


def main():
    with tempfile.TemporaryDirectory() as home, \
         tempfile.TemporaryDirectory() as ws_a, \
         tempfile.TemporaryDirectory() as ws_b:
        # register two projects, each bound to its own workspace
        run(ws_a, "alice", "project", "init", "--name", "projA", home=home)
        run(ws_b, "bob", "project", "init", "--name", "projB", home=home)

        # alice (in ws_a) sends without --project → auto-scoped to projA
        rc, out = run(ws_a, "alice", "send", "--to", "bob", "--subject", "taskA", home=home)
        assert rc == 0 and "sent task" in out, f"send failed: {out}"

        # bob in ws_B does NOT see taskA (he's in projB)
        rc, out = run(ws_b, "bob", "inbox", home=home)
        assert "taskA" not in out, f"LEAK: bob saw taskA from another project: {out}"

        # bob in ws_A (same workspace as alice) DOES see it
        rc, out = run(ws_a, "bob", "inbox", home=home)
        assert "taskA" in out, f"bob in shared workspace should see taskA: {out}"

        # alice in ws_B trying to force --project projA → refused (exit 2)
        rc, out = run(ws_b, "alice", "board", "--project", "projA", home=home)
        assert rc == 2 and "refusing cross-project" in out, f"isolation not enforced: rc={rc} {out}"

    print("✅ isolation OK: cwd-bound projects, cross-project access refused, same-workspace shared")


if __name__ == "__main__":
    main()
