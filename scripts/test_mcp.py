#!/usr/bin/env python3
"""Self-check: two agents exchange a task through the MCP server over the
shared file board. Run: python3 test_mcp.py  (uses a throwaway AGENT_BRIDGE_HOME)."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

MCP = str(Path(__file__).with_name("bridge_mcp.py"))


def session(name, calls, home):
    """Run one MCP stdio session as `name`, return list of tool-call result texts."""
    env = dict(os.environ, AGENT_BRIDGE_HOME=home)
    reqs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ]
    for i, (tool, args) in enumerate(calls, start=2):
        reqs.append({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                     "params": {"name": tool, "arguments": args}})
    p = subprocess.Popen([sys.executable, MCP, "--as", name],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)
    out, _ = p.communicate("".join(json.dumps(r) + "\n" for r in reqs), timeout=30)
    results = {}
    for line in out.splitlines():
        if not line.strip():
            continue
        msg = json.loads(line)
        if msg.get("id", 0) >= 2 and "result" in msg:
            results[msg["id"]] = msg["result"]["content"][0]["text"]
    return results


def main():
    with tempfile.TemporaryDirectory() as home:
        # alice delegates a review task to bob
        a = session("alice", [("bridge_send", {"to": "bob", "subject": "review PR 42"})], home)
        assert "sent task" in a[2], f"send failed: {a[2]}"
        task_id = a[2].split("sent task ")[1].split()[0]

        # bob sees it in his inbox, claims, completes
        b = session("bob", [
            ("bridge_inbox", {}),
            ("bridge_claim", {"task_id": task_id}),
            ("bridge_done", {"task_id": task_id, "result": "LGTM"}),
        ], home)
        assert "review PR 42" in b[2], f"bob inbox missing task: {b[2]}"
        assert "claimed" in b[3], f"claim failed: {b[3]}"
        assert "completed" in b[4], f"done failed: {b[4]}"

        # alice sees it completed on the board + in the activity feed
        a2 = session("alice", [("bridge_board", {}), ("bridge_activity", {})], home)
        assert "completed" in a2[2] and task_id in a2[2], f"board wrong: {a2[2]}"
        assert "bob done" in a2[3] and "review PR 42" in a2[3], f"activity missing event: {a2[3]}"

    print("✅ MCP round-trip OK: alice→bob send/claim/done/board/activity all flow through MCP")


if __name__ == "__main__":
    main()
