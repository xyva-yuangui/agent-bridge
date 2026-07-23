#!/usr/bin/env python3
"""agent-bridge MCP server (stdio, JSON-RPC 2.0) — zero external deps.

Wraps bridge.py so Codex / Reasonix / Claude Code / ZCode (any MCP client)
can call the same shared file board over MCP instead of shelling out.

Identity: pass --as <name> (each tool's MCP config sets it), or rely on
AGENT_BRIDGE_NAME in the inherited env.

ponytail: thin subprocess wrapper around bridge.py — reuses ALL logic, no
refactor. One process per call is fine at agent-interaction rate.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

BRIDGE = str(Path(__file__).with_name("bridge.py"))
PROTOCOL_VERSION = "2024-11-05"
BRIDGE_VERSION = "1.3.0"

# tool -> bridge subcommand spec. pos = positional argv, flags = --flag args.
TOOLS = {
    "bridge_status": {
        "description": "Check your agent-bridge inbox. Call at the START of every turn; if it reports pending tasks, run bridge_inbox and handle them before replying to the user.",
        "sub": "status", "pos": [], "flags": ["oneliner"],
        "schema": {"type": "object", "properties": {
            "oneliner": {"type": "boolean", "description": "single-line summary"}}},
    },
    "bridge_inbox": {
        "description": "List tasks that need your action right now (assigned to you, or questions/reviews awaiting you).",
        "sub": "inbox", "pos": [], "flags": ["project"],
        "schema": {"type": "object", "properties": {
            "project": {"type": "string"}}},
    },
    "bridge_send": {
        "description": "Send/delegate a task to another agent. Give --to a name, or --skill to auto-route to the best agent for that capability (architecture|hard-reasoning|complex-impl|orchestrate|review|refactor|plan|headless|frontend|ui). Auto-wakes the target by default.",
        "sub": "send", "pos": [], "flags": ["to", "skill", "subject", "body", "files", "project", "no_wake"],
        "schema": {"type": "object", "required": ["subject"], "properties": {
            "to": {"type": "string", "description": "target agent name"},
            "skill": {"type": "string", "description": "capability tag for auto-routing (use instead of --to)"},
            "subject": {"type": "string"},
            "body": {"type": "string"},
            "files": {"type": "string", "description": "comma-separated paths"},
            "project": {"type": "string"},
            "no_wake": {"type": "boolean", "description": "skip auto-waking the target (default: false, i.e. always wake)"}}},
    },
    "bridge_claim": {
        "description": "Claim a task assigned to you (moves it to working).",
        "sub": "claim", "pos": ["task_id"], "flags": ["project"],
        "schema": {"type": "object", "required": ["task_id"], "properties": {
            "task_id": {"type": "string"}, "project": {"type": "string"}}},
    },
    "bridge_done": {
        "description": "Mark a task you own as completed, with a result summary.",
        "sub": "done", "pos": ["task_id"], "flags": ["result", "files", "project"],
        "schema": {"type": "object", "required": ["task_id", "result"], "properties": {
            "task_id": {"type": "string"}, "result": {"type": "string"},
            "files": {"type": "string"}, "project": {"type": "string"}}},
    },
    "bridge_show": {
        "description": "Show ALL detail of one task — body, files, question, answer, result, review comment. Use this before working a task; inbox/board only show the subject.",
        "sub": "show", "pos": ["task_id"], "flags": ["project"],
        "schema": {"type": "object", "required": ["task_id"], "properties": {
            "task_id": {"type": "string"}, "project": {"type": "string"}}},
    },
    "bridge_board": {
        "description": "Show the full task board (all tasks, owners, statuses).",
        "sub": "board", "pos": [], "flags": ["project"],
        "schema": {"type": "object", "properties": {"project": {"type": "string"}}},
    },
    "bridge_question": {
        "description": "Ask a clarifying question back to the sender (blocks the task until answered).",
        "sub": "question", "pos": ["task_id"], "flags": ["body", "project"],
        "schema": {"type": "object", "required": ["task_id", "body"], "properties": {
            "task_id": {"type": "string"}, "body": {"type": "string"}, "project": {"type": "string"}}},
    },
    "bridge_answer": {
        "description": "Answer a question on a task you sent (unblocks it).",
        "sub": "answer", "pos": ["task_id"], "flags": ["body", "project"],
        "schema": {"type": "object", "required": ["task_id", "body"], "properties": {
            "task_id": {"type": "string"}, "body": {"type": "string"}, "project": {"type": "string"}}},
    },
    "bridge_review": {
        "description": "Request a review (omit verdict) or issue one (--verdict approve|changes).",
        "sub": "review", "pos": ["task_id"], "flags": ["verdict", "body", "project"],
        "schema": {"type": "object", "required": ["task_id"], "properties": {
            "task_id": {"type": "string"},
            "verdict": {"type": "string", "enum": ["approve", "changes"]},
            "body": {"type": "string"}, "project": {"type": "string"}}},
    },
    "bridge_wake": {
        "description": "Wake an idle agent so it checks its inbox now (only works if that agent registered a headless wake command, e.g. Reasonix).",
        "sub": "wake", "pos": ["agent"], "flags": [],
        "schema": {"type": "object", "required": ["agent"], "properties": {
            "agent": {"type": "string"}}},
    },
    "bridge_agents": {
        "description": "Show the agent capability matrix (who is good at what) for routing decisions.",
        "sub": "agents", "pos": [], "flags": [],
        "schema": {"type": "object", "properties": {}},
    },
    "bridge_activity": {
        "description": "Show the activity feed (what each agent has done) to catch up after switching agents.",
        "sub": "activity", "pos": [], "flags": ["since", "project"],
        "schema": {"type": "object", "properties": {
            "since": {"type": "string"}, "project": {"type": "string"}}},
    },
    "bridge_context": {
        "description": "Read shared project context/decisions (--show) or append to it (--add).",
        "sub": "context", "pos": [], "flags": ["show", "add", "project"],
        "schema": {"type": "object", "properties": {
            "show": {"type": "boolean"}, "add": {"type": "string"}, "project": {"type": "string"}}},
    },
    "bridge_clean": {
        "description": "Clean up old completed/failed/canceled tasks. Use --all to remove all, --days N to remove older than N days, --dry-run to preview.",
        "sub": "clean", "pos": [], "flags": ["days", "all", "status", "dry_run", "project"],
        "schema": {"type": "object", "properties": {
            "days": {"type": "integer", "description": "Remove tasks older than N days"},
            "all": {"type": "boolean", "description": "Remove all completed/failed/canceled tasks regardless of age"},
            "status": {"type": "string", "description": "Comma-separated statuses to clean (default: completed,failed,canceled)"},
            "dry_run": {"type": "boolean", "description": "Preview without deleting"},
            "project": {"type": "string"}}},
    },
    "bridge_doctor": {
        "description": "Check agent-bridge storage, profiles, hooks, and application integration readiness.",
        "sub": "doctor", "pos": [], "flags": [],
        "schema": {"type": "object", "properties": {}},
    },
    "bridge_project": {
        "description": "Initialize, list, or show an agent-bridge project.",
        "sub": "project", "pos": ["action"], "flags": ["name", "workspace", "goal"],
        "schema": {"type": "object", "required": ["action"], "properties": {
            "action": {"type": "string", "enum": ["init", "list", "show"]},
            "name": {"type": "string"}, "workspace": {"type": "string"},
            "goal": {"type": "string"}}},
    },
    "bridge_whoami": {
        "description": "Show the identity used for agent-bridge calls.",
        "sub": "whoami", "pos": [], "flags": [],
        "schema": {"type": "object", "properties": {}},
    },
    "bridge_who_coordinates": {
        "description": "Show the current coordinator for a project.",
        "sub": "who-coordinates", "pos": [], "flags": ["project"],
        "schema": {"type": "object", "properties": {
            "project": {"type": "string"}}},
    },
    "bridge_log": {
        "description": "Append a manual event to the shared activity feed.",
        "sub": "log", "pos": [], "flags": ["what", "project"],
        "schema": {"type": "object", "required": ["what"], "properties": {
            "what": {"type": "string"}, "project": {"type": "string"}}},
    },
}


def _is_bool_flag(spec: dict, flag: str) -> bool:
    return spec.get("schema", {}).get("properties", {}).get(flag, {}).get("type") == "boolean"


def _truthy(v) -> bool:
    return v is True or (isinstance(v, str) and v.strip().lower() in ("true", "1", "yes"))


def build_argv(spec: dict, args: dict, identity: str) -> list:
    av = [sys.executable, BRIDGE]
    if identity:
        av += ["--as", identity]
    av.append(spec["sub"])
    for p in spec["pos"]:
        if args.get(p) is not None:
            av.append(str(args[p]))
    for f in spec["flags"]:
        v = args.get(f)
        if v is None:
            continue
        flag = "--" + f.replace("_", "-")
        # store_true flags: presence only (models may send bool OR the string "true")
        if _is_bool_flag(spec, f):
            if _truthy(v):
                av.append(flag)
        else:
            av += [flag, str(v)]
    return av


def respond(rid, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": rid}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle(req: dict, identity: str):
    method = req.get("method")
    rid = req.get("id")
    if method == "initialize":
        respond(rid, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "agent-bridge", "version": BRIDGE_VERSION},
        })
    elif method == "notifications/initialized":
        pass  # notification, no reply
    elif method == "ping":
        respond(rid, {})
    elif method == "tools/list":
        respond(rid, {"tools": [
            {"name": n, "description": t["description"], "inputSchema": t["schema"]}
            for n, t in TOOLS.items()
        ]})
    elif method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        spec = TOOLS.get(name)
        if not spec:
            respond(rid, error={"code": -32602, "message": f"unknown tool: {name}"})
            return
        try:
            env = os.environ.copy()
            env.setdefault("PYTHONUTF8", "1")
            r = subprocess.run(
                build_argv(spec, args, identity),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=40,
                env=env,
            )
            out = ((r.stdout or "") + (r.stderr or "")).strip() or "(no output)"
            respond(rid, {"content": [{"type": "text", "text": out}], "isError": r.returncode != 0})
        except Exception as e:
            respond(rid, {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True})
    elif rid is not None:
        respond(rid, error={"code": -32601, "message": f"method not found: {method}"})


def main():
    identity = os.environ.get("AGENT_BRIDGE_NAME", "").strip()
    argv = sys.argv[1:]
    if "--as" in argv:
        identity = argv[argv.index("--as") + 1]
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        handle(req, identity)


if __name__ == "__main__":
    main()
