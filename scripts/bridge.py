#!/usr/bin/env python3
"""agent-bridge — cross-agent collaboration for personal devs."""

from __future__ import annotations  # 3.9 compat for `str | None` annotations

import argparse
import calendar
try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False

if not _HAS_FCNTL:
    def _portable_lock(filepath, timeout=10):
        lockpath = filepath + ".lock"
        deadline = time.time() + timeout
        while True:
            try:
                fd = os.open(lockpath, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return lockpath
            except (FileExistsError, OSError):
                if time.time() > deadline:
                    raise TimeoutError(f"Could not acquire lock on {filepath}")
                time.sleep(0.01)

    def _portable_unlock(lockpath):
        try:
            os.unlink(lockpath)
        except OSError:
            pass

from contextlib import contextmanager

@contextmanager
def _locked_file(path, mode):
    if _HAS_FCNTL:
        lock_op = fcntl.LOCK_EX if ("w" in mode or "+" in mode) else fcntl.LOCK_SH
        with open(path, mode) as f:
            fcntl.flock(f, lock_op)
            try:
                yield f
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    else:
        lockpath = _portable_lock(path)
        try:
            with open(path, mode) as f:
                yield f
        finally:
            _portable_unlock(lockpath)
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

# ── push layer ──────────────────────────────────────────────────────────────────
# Pull model can't deliver to an idle agent. Two best-effort nudges:
#  (a) desktop notification on send → the human switches to the target agent;
#  (b) headless "wake" → if the target registered a wake command (e.g. Reasonix's
#      `reasonix run`), run it so the agent checks its inbox now. Never fatal.

def _desktop_notify(title: str, msg: str):
    try:
        if sys.platform == "darwin":
            subprocess.run(["osascript", "-e",
                            f"display notification {json.dumps(msg)} with title {json.dumps(title)}"],
                           capture_output=True, timeout=5)
        elif sys.platform == "win32":
            subprocess.run(["msg", "*", f"{title}: {msg}"], capture_output=True, timeout=5)
        else:
            subprocess.run(["notify-send", title, msg], capture_output=True, timeout=5)
    except Exception:
        pass


def _wake_agent(name: str) -> bool:
    """Run the target's registered headless wake command, if any. Backgrounded."""
    af = AGENTS_DIR / name / "agent.json"
    if not af.exists():
        return False
    try:
        wake = json.load(open(af)).get("wake")
    except Exception:
        wake = None
    if not wake:
        return False
    prompt = (
        "Run `bridge inbox`. Claim ALL pending tasks. "
        "Complete each one, marking done with `bridge done`. "
        "Keep going until `bridge inbox` is empty. "
        "Then report a summary of what you completed."
    )
    try:
        env = os.environ.copy()
        env["AGENT_BRIDGE_NAME"] = name
        kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "env": env}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(wake.split() + [prompt], **kwargs)
        return True
    except Exception:
        return False

# ── identity ──────────────────────────────────────────────────────────────────

def resolve_identity() -> str:
    name = os.environ.get("AGENT_BRIDGE_NAME", "").strip()
    if name:
        return name
    return ""


def agent_dir(name: str) -> Path:
    return BASE_DIR / "agents" / name


# ── shared state ──────────────────────────────────────────────────────────────

BASE_DIR = Path(os.environ.get("AGENT_BRIDGE_HOME", Path.home() / ".agent-bridge"))
AGENTS_DIR = BASE_DIR / "agents"
PROJECTS_DIR = BASE_DIR / "projects"

# ponytail: coordinator — first agent to use bridge in a project becomes the coordinator.
# The coordinator's model decides routing based on agent capabilities, not static rules.

def get_coordinator(project_id: str) -> str | None:
    """Return the coordinator for this project, or None."""
    bp = board_path(project_id)
    if not bp.exists():
        return None
    board = read_board(bp)
    return board.get("coordinator")


def set_coordinator(project_id: str, name: str):
    """Set the coordinator for this project (first agent to use it)."""
    def _set(board):
        if not board.get("coordinator"):
            board["coordinator"] = name
        return board
    atomic_update_board(board_path(project_id), _set)


def load_capabilities() -> dict:
    """Read all agents' capabilities from their profiles."""
    caps = {}
    for af in sorted(AGENTS_DIR.glob("*/agent.json")):
        try:
            ad = json.load(open(af))
            name = ad.get("name", af.parent.name)
            skills = ad.get("skills", [])
            if skills:
                caps[name] = skills
        except Exception:
            pass
    return caps


def route_task(skill: str, exclude: str = "") -> str | None:
    """Find the best agent for a skill. Returns None if no match."""
    caps = load_capabilities()
    for name, skills in sorted(caps.items()):
        if name == exclude:
            continue
        if skill in skills:
            return name
    return None


def ensure_dirs():
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


# ── board operations ──────────────────────────────────────────────────────────

BOARD_VERSION = 1
VALID_STATES = {"pending", "accepted", "working", "completed", "failed", "canceled",
                "input_required", "review_requested", "review_approved", "changes_requested"}
# ponytail: activity.jsonl rotation — keep it bounded so hook status never times out
MAX_ACTIVITY_ENTRIES = 10000
# ponytail: auto-cleanup — prevent board.json from growing unbounded
MAX_COMPLETED_TASKS = 50       # auto-archive oldest completed when exceeded
STALE_WORKING_HOURS = 24       # auto-fail working tasks stuck > this long
MAX_INBOX_AGE_DAYS = 30        # don't show tasks older than this in inbox
AUTO_CLEAN_DAYS = 7            # silently clean completed tasks older than this (every status call)
AUTO_CLEAN_MIN_TASKS = 10      # only trigger auto-clean when board has at least this many tasks


def board_path(project_id: str = "default") -> Path:
    return PROJECTS_DIR / project_id / "board.json"


def activity_path(project_id: str = "default") -> Path:
    return PROJECTS_DIR / project_id / "activity.jsonl"


# ── project isolation ───────────────────────────────────────────────────────────
# Security model: a project bound to a workspace dir can ONLY be accessed from
# inside that dir. Two agents can collaborate iff their cwd resolves to the same
# project. Single OS user → this is correctness/scoping, not adversarial auth.
# ponytail: cwd-prefix match like git repo discovery; "default" (unbound) stays open.

def _under(child: str, parent: str) -> bool:
    c = os.path.normcase(os.path.normpath(child))
    p = os.path.normcase(os.path.normpath(parent))
    return c == p or c.startswith(p + os.sep)


def project_workspace(pid: str) -> str | None:
    pj = PROJECTS_DIR / pid / "project.json"
    if pj.exists():
        try:
            return json.load(open(pj)).get("workspace") or None
        except Exception:
            return None
    return None


def resolve_project(explicit: str | None) -> str:
    """Explicit --project wins; else derive from cwd (longest workspace prefix); else 'default'."""
    if explicit:
        return explicit
    cwd = str(Path.cwd().resolve())
    best = None  # (pid, len)
    for pj in PROJECTS_DIR.glob("*/project.json"):
        try:
            ws = json.load(open(pj)).get("workspace", "")
        except Exception:
            continue
        if not ws:
            continue
        wsr = str(Path(ws).resolve())
        if _under(cwd, wsr) and (best is None or len(wsr) > best[1]):
            best = (pj.parent.name, len(wsr))
    return best[0] if best else "default"


def enforce_workspace(pid: str):
    """Refuse access to a workspace-bound project from outside its workspace."""
    ws = project_workspace(pid)
    if not ws:
        return  # unbound project (e.g. "default") is open
    wsr = str(Path(ws).resolve())
    cwd = str(Path.cwd().resolve())
    if not _under(cwd, wsr):
        print(f"🔒 project '{pid}' is bound to {wsr}; you are in {cwd} — refusing cross-project access",
              file=sys.stderr)
        sys.exit(2)


def _project(args) -> str:
    """Resolve + enforce the active project for a command."""
    pid = resolve_project(getattr(args, "project", None))
    enforce_workspace(pid)
    return pid


def read_board(path: Path) -> dict:
    """Read board.json with shared lock (for read-only operations)."""
    if not path.exists():
        return {"version": BOARD_VERSION, "tasks": []}
    with _locked_file(str(path), "r") as f:
        return json.load(f)


def atomic_update_board(path: Path, update_fn):
    """Hold exclusive lock across read-modify-write. update_fn(board) -> board."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with open(path, "w") as f:
            json.dump({"version": BOARD_VERSION, "tasks": []}, f)
    # ponytail: open with r+ to read AND write under one lock
    with _locked_file(str(path), "r+") as f:
        f.seek(0)
        board = json.load(f)
        board = update_fn(board)
        board["version"] = BOARD_VERSION
        f.seek(0)
        f.truncate()
        json.dump(board, f, indent=2)
        f.flush()
        os.fsync(f.fileno())


def write_board(path: Path, data: dict):
    """Write board.json with exclusive lock + atomic rename (for new boards)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    data["version"] = BOARD_VERSION
    with _locked_file(str(tmp), "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def append_activity(project_id: str, entry: dict):
    """Append to activity.jsonl with rotation."""
    ap = activity_path(project_id)
    ap.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with _locked_file(str(ap), "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    # ponytail: rotate if over limit
    _maybe_rotate(ap)


def _maybe_rotate(ap: Path):
    """Truncate activity.jsonl to half if over MAX_ACTIVITY_ENTRIES."""
    if not ap.exists():
        return
    with _locked_file(str(ap), "r") as f:
        lines = f.readlines()
    if len(lines) <= MAX_ACTIVITY_ENTRIES:
        return
    keep = lines[len(lines) // 2:]
    tmp = ap.with_suffix(".tmp")
    with _locked_file(str(tmp), "w") as f:
        f.writelines(keep)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, ap)


def _touch_heartbeat(name: str):
    """Update agent heartbeat timestamp WITHOUT clobbering strengths/skills."""
    af = agent_dir(name) / "agent.json"
    af.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if af.exists():
        try:
            data = json.load(open(af))
        except Exception:
            data = {}
    data["name"] = name
    data["last_seen"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(af, "w") as f:
        json.dump(data, f, indent=2)


def _new_task_id() -> str:
    return uuid.uuid4().hex[:12]


def _task_status(task: dict) -> str:
    return task.get("status", "pending")


def _inbox_filter(task: dict, me: str, max_age_days: int = MAX_INBOX_AGE_DAYS) -> bool:
    """Task is in my inbox if it needs my action this turn."""
    status = _task_status(task)
    # ponytail: skip tasks older than max_age_days to avoid zombie history in inbox
    created = task.get("created", "")
    if created and max_age_days > 0:
        try:
            ct = calendar.timegm(time.strptime(created, "%Y-%m-%dT%H:%M:%SZ"))
            cutoff = time.time() - max_age_days * 86400
            if ct < cutoff:
                return False
        except (ValueError, OverflowError):
            pass  # malformed date — include it rather than silently drop
    # questions and review requests go back to the original sender to act on
    if status in ("input_required", "review_requested"):
        return task.get("from") == me
    if task.get("to") != me:
        return False
    # work to do as the assignee: a new task, or rework after a 'changes' review
    return status in ("pending", "changes_requested")


def _auto_stale_working(project_id: str, me: str):
    """Detect and auto-fail working tasks that have been stuck too long."""
    bp = board_path(project_id)
    if not bp.exists():
        return
    cutoff = time.time() - STALE_WORKING_HOURS * 3600
    stale_ids = []

    def _detect(board):
        for t in board["tasks"]:
            if t["status"] != "working":
                continue
            updated = t.get("updated", "")
            if not updated:
                continue
            try:
                ut = calendar.timegm(time.strptime(updated, "%Y-%m-%dT%H:%M:%SZ"))
                if ut < cutoff:
                    t["status"] = "failed"
                    t["result"] = f"auto-failed: stuck in working for >{STALE_WORKING_HOURS}h"
                    t["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    stale_ids.append(t["id"])
            except (ValueError, OverflowError):
                pass
        return board

    if stale_ids:
        atomic_update_board(bp, _detect)
        for tid in stale_ids:
            append_activity(project_id, {
                "agent": me, "action": "auto-failed", "task_id": tid,
                "subject": f"stale working task (> {STALE_WORKING_HOURS}h)",
            })


def _auto_archive(project_id: str, me: str):
    """Archive oldest completed tasks when board exceeds MAX_COMPLETED_TASKS."""
    bp = board_path(project_id)
    if not bp.exists():
        return
    ap = bp.parent / "archive.json"

    def _archive(board):
        completed = [t for t in board["tasks"] if t["status"] in ("completed", "failed", "canceled")]
        if len(completed) <= MAX_COMPLETED_TASKS:
            return board  # nothing to do
        # sort by creation time, archive oldest half of completed
        completed.sort(key=lambda x: x.get("created", ""))
        to_archive = completed[:len(completed) // 2]
        archive_ids = {t["id"] for t in to_archive}
        # load existing archive
        existing = []
        if ap.exists():
            try:
                existing = json.load(open(ap))
            except Exception:
                existing = []
        existing.extend(to_archive)
        with open(ap, "w") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)
        # remove archived from board
        board["tasks"] = [t for t in board["tasks"] if t["id"] not in archive_ids]
        return board

    board = read_board(bp)
    completed = [t for t in board["tasks"] if t["status"] in ("completed", "failed", "canceled")]
    if len(completed) > MAX_COMPLETED_TASKS:
        atomic_update_board(bp, _archive)
        append_activity(project_id, {
            "agent": me, "action": "auto-archive",
            "subject": f"archived {len(completed) // 2} completed tasks (board had {len(completed)})",
        })


def _auto_clean(project_id: str, me: str):
    """Silently clean old completed tasks every status call — transparent maintenance."""
    bp = board_path(project_id)
    if not bp.exists():
        return
    board = read_board(bp)
    total = len(board["tasks"])
    if total < AUTO_CLEAN_MIN_TASKS:
        return  # board is small, no need to clean
    cutoff = time.time() - AUTO_CLEAN_DAYS * 86400
    cleaned = 0

    def _clean(board):
        nonlocal cleaned
        kept = []
        for t in board["tasks"]:
            if t["status"] not in ("completed", "failed", "canceled"):
                kept.append(t)
                continue
            updated = t.get("updated", "")
            try:
                ut = calendar.timegm(time.strptime(updated, "%Y-%m-%dT%H:%M:%SZ"))
                if ut >= cutoff:
                    kept.append(t)
                    continue
            except (ValueError, OverflowError):
                kept.append(t)
                continue
            cleaned += 1
        board["tasks"] = kept
        return board

    # pre-check: count how many would be cleaned, skip if none
    would_clean = 0
    for t in board["tasks"]:
        if t["status"] not in ("completed", "failed", "canceled"):
            continue
        updated = t.get("updated", "")
        try:
            ut = calendar.timegm(time.strptime(updated, "%Y-%m-%dT%H:%M:%SZ"))
            if ut < cutoff:
                would_clean += 1
        except (ValueError, OverflowError):
            pass
    if would_clean == 0:
        return

    atomic_update_board(bp, _clean)
    if cleaned > 0:
        append_activity(project_id, {
            "agent": me, "action": "auto-clean",
            "subject": f"silently cleaned {cleaned} old task(s) (> {AUTO_CLEAN_DAYS}d)",
        })


def cmd_clean(args):
    name = _get_name(args)
    ensure_dirs()
    pid = _project(args)
    bp = board_path(pid)
    if not bp.exists():
        print("📭 board is empty")
        return

    clean_statuses = {"completed", "failed", "canceled"}
    if args.status:
        clean_statuses = set(s.strip() for s in args.status.split(","))
        invalid = clean_statuses - VALID_STATES
        if invalid:
            print(f"❌ invalid statuses: {', '.join(invalid)}", file=sys.stderr)
            sys.exit(1)

    cutoff = None
    if not args.clean_all and args.days is not None:
        cutoff = time.time() - args.days * 86400
    # --all means no cutoff (clean everything in the status set)

    def _clean(board):
        removed = []
        kept = []
        for t in board["tasks"]:
            if t["status"] not in clean_statuses:
                kept.append(t)
                continue
            if cutoff is not None:
                updated = t.get("updated", "")
                try:
                    ut = calendar.timegm(time.strptime(updated, "%Y-%m-%dT%H:%M:%SZ"))
                    if ut >= cutoff:
                        kept.append(t)
                        continue
                except (ValueError, OverflowError):
                    pass
            removed.append(t)
        if args.dry_run:
            # restore all tasks for preview
            return board
        board["tasks"] = kept
        # archive removed tasks
        if removed and not args.dry_run:
            ap = bp.parent / "archive.json"
            existing = []
            if ap.exists():
                try:
                    existing = json.load(open(ap))
                except Exception:
                    existing = []
            existing.extend(removed)
            with open(ap, "w") as f:
                json.dump(existing, f, indent=2, ensure_ascii=False)
        return board, removed

    if args.dry_run:
        board = read_board(bp)
        removed = []
        for t in board["tasks"]:
            if t["status"] not in clean_statuses:
                continue
            if cutoff is not None:
                updated = t.get("updated", "")
                try:
                    ut = calendar.timegm(time.strptime(updated, "%Y-%m-%dT%H:%M:%SZ"))
                    if ut >= cutoff:
                        continue
                except (ValueError, OverflowError):
                    pass
            removed.append(t)
        if not removed:
            print("📭 nothing to clean")
        else:
            print(f"🔍 dry-run: {len(removed)} task(s) would be removed:")
            for t in removed:
                print(f"  [{t['id']}] [{t['status']}] {t['subject']}")
    else:
        board = read_board(bp)
        removed = []
        kept = []
        for t in board["tasks"]:
            if t["status"] not in clean_statuses:
                kept.append(t)
                continue
            if cutoff is not None:
                updated = t.get("updated", "")
                try:
                    ut = calendar.timegm(time.strptime(updated, "%Y-%m-%dT%H:%M:%SZ"))
                    if ut >= cutoff:
                        kept.append(t)
                        continue
                except (ValueError, OverflowError):
                    pass
            removed.append(t)
        if not removed:
            print("📭 nothing to clean")
            return
        # write back
        def _write(board):
            board["tasks"] = kept
            return board
        atomic_update_board(bp, _write)
        # archive
        if removed:
            ap = bp.parent / "archive.json"
            existing = []
            if ap.exists():
                try:
                    existing = json.load(open(ap))
                except Exception:
                    existing = []
            existing.extend(removed)
            with open(ap, "w") as f:
                json.dump(existing, f, indent=2, ensure_ascii=False)
        append_activity(pid, {
            "agent": name, "action": "clean",
            "subject": f"cleaned {len(removed)} task(s)",
        })
        print(f"✅ cleaned {len(removed)} task(s), archived to {ap}")


# ── commands ──────────────────────────────────────────────────────────────────

def _get_name(args) -> str:
    name = args.as_ or resolve_identity()
    if not name:
        print("error: no identity — set AGENT_BRIDGE_NAME or use --as <name>", file=sys.stderr)
        sys.exit(1)
    return name


def cmd_whoami(args):
    print(_get_name(args))


def cmd_doctor(args):
    name = _get_name(args)
    ok = True
    warnings = 0
    print(f"✅ identity: {name}")

    ensure_dirs()
    for label, p in [("base dir", BASE_DIR), ("agents dir", AGENTS_DIR), ("projects dir", PROJECTS_DIR)]:
        if os.access(p, os.W_OK):
            print(f"✅ {label}: {p} (writable)")
        else:
            print(f"❌ {label}: {p} (not writable)")
            ok = False

    # board.json version
    bp = board_path()
    if bp.exists():
        board = read_board(bp)
        if board.get("version") == BOARD_VERSION:
            print(f"✅ board.json: version {BOARD_VERSION}")
        else:
            print(f"⚠️  board.json: version {board.get('version')} (expected {BOARD_VERSION})")
            warnings += 1
    else:
        print("ℹ️  board.json: not yet created")

    # agent identity uniqueness
    agents = list(AGENTS_DIR.glob("*/agent.json"))
    seen = {}
    dup = False
    for af in agents:
        try:
            ad = json.load(open(af))
            aname = ad.get("name", af.parent.name)
            if aname in seen:
                print(f"⚠️  duplicate agent name: {aname} ({af} and {seen[aname]})")
                dup = True
                warnings += 1
            seen[aname] = af
        except Exception:
            pass
    if not dup and agents:
        print(f"✅ agent identities unique ({len(agents)} agents)")

    # heartbeat recency (default 30 min)
    hb_deadline = time.time() - 1800
    stale = 0
    for af in agents:
        try:
            ad = json.load(open(af))
            aname = ad.get("name", af.parent.name)
            ts = ad.get("last_seen", "")
            if ts:
                t = calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))  # ts is UTC
                if t < hb_deadline:
                    print(f"⚠️  {aname}: last seen {ts} (stale >30min)")
                    stale += 1
                    warnings += 1
                else:
                    print(f"✅ {aname}: heartbeat OK ({ts})")
            else:
                print(f"⚠️  {aname}: no heartbeat data")
                warnings += 1
        except Exception:
            pass
    if stale == 0 and agents:
        print("✅ all agents have recent heartbeats")

    # Reasonix-specific: check system_prompt_file for directive
    _check_reasonix_config(name, ok, warnings)

    # skill path exclusion check
    excluded = _check_skill_excluded()
    if excluded:
        print(f"⚠️  skill path excluded: {excluded}")
        warnings += 1
    else:
        print("✅ skill path not excluded")

    # hook script
    hook_path = Path(__file__).resolve()
    if hook_path.exists() and os.access(hook_path, os.X_OK):
        print(f"✅ hook script: {hook_path}")
    else:
        print(f"⚠️  hook script not executable: {hook_path}")
        warnings += 1

    if ok and warnings == 0:
        print("✅ agent-bridge is ready")
    elif ok:
        print(f"⚠️  agent-bridge: {warnings} warning(s) — see above")
    else:
        sys.exit(1)


def _check_reasonix_config(name: str, ok: bool, warnings: int):
    """Check Reasonix-specific config if this is a Reasonix agent."""
    # ponytail: only check if reasonix config exists — skip otherwise
    reasonix_config = Path.home() / ".reasonix" / "config.toml"
    if not reasonix_config.exists():
        return
    try:
        content = reasonix_config.read_text()
        if "agent-bridge" not in content and "bridge status" not in content:
            print(f"⚠️  Reasonix: system_prompt_file may not contain agent-bridge directive")
            warnings += 1
    except Exception:
        pass


def _check_skill_excluded():
    """Check if agent-bridge skill path is excluded in Reasonix config."""
    reasonix_config = Path.home() / ".reasonix" / "config.toml"
    if not reasonix_config.exists():
        return None
    try:
        content = reasonix_config.read_text()
        if "agent-bridge" in content and "excluded_paths" in content:
            import re
            # ponytail: simple grep — if excluded_paths line contains agent-bridge, flag it
            for line in content.split("\n"):
                if "excluded_paths" in line and "agent-bridge" in line:
                    return line.strip()
    except Exception:
        pass
    return None


def cmd_status(args):
    name = _get_name(args)
    ensure_dirs()
    _touch_heartbeat(name)  # ponytail: heartbeat on every status call
    pid = _project(args)  # hook runs in the agent's cwd → scope to that project
    # ponytail: auto-fail stale working tasks before checking inbox
    _auto_stale_working(pid, name)
    # ponytail: silently clean old completed tasks every turn
    _auto_clean(pid, name)
    bp = board_path(pid)
    board = read_board(bp)
    pending = [t for t in board["tasks"] if _inbox_filter(t, name)]
    n = len(pending)
    tag = "" if pid == "default" else f" [{pid}]"
    if args.oneliner:
        if n == 0:
            print(f"📭 agent-bridge{tag}: no pending tasks for {name}")
        else:
            senders = {t["from"] for t in pending}
            print(f"📥 agent-bridge{tag}: {n} pending (from {', '.join(sorted(senders))}) — run bridge inbox")
    else:
        print(f"agent-bridge{tag}: I am {name}, {n} pending, {len(board['tasks'])} total on board")


def cmd_send(args):
    name = _get_name(args)
    ensure_dirs()
    pid = _project(args)
    bp = board_path(pid)
    # ponytail: first agent to send in a project becomes the coordinator
    set_coordinator(pid, name)
    # routing is the coordinator MODEL's call (read `bridge agents` + project context).
    # --skill is only an optional convenience fallback, not a hard rule.
    target = args.to
    if args.skill and not target:
        target = route_task(args.skill, exclude=name)
        if not target:
            print(f"❌ no agent tagged '{args.skill}'. Use --to <agent>, or read `bridge agents` and decide.", file=sys.stderr)
            sys.exit(1)
        print(f"🎯 fallback-routed to {target} (skill: {args.skill}) — override with --to if the project needs someone else")
    if not target:
        print("❌ must specify --to <agent> or --skill <tag>", file=sys.stderr)
        sys.exit(1)
    task = {
        "id": _new_task_id(),
        "subject": args.subject,
        "body": args.body or "",
        "from": name,
        "to": target,
        "status": "pending",
        "skill": args.skill or "",
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "files": args.files.split(",") if args.files else [],
        "project": pid,
    }
    def _append(board):
        board["tasks"].append(task)
        return board
    atomic_update_board(bp, _append)
    print(f"✅ sent task {task['id']} to {target}: {args.subject}")
    # push layer: notify the human, and auto-wake the target agent (unless --no-wake)
    _desktop_notify(f"agent-bridge → {target}", args.subject)
    no_wake = getattr(args, "no_wake", False)
    if not no_wake:
        if _wake_agent(target):
            print(f"⏰ woke {target} (headless) to handle it now")
        else:
            print(f"ℹ️  {target} has no headless wake command — it'll see this next turn")


def cmd_wake(args):
    ensure_dirs()
    if _wake_agent(args.agent):
        print(f"⏰ woke {args.agent} (headless) — it will check its inbox")
    else:
        print(f"ℹ️  {args.agent} has no headless wake command registered (install with --wake-cmd)")


def cmd_who_coordinates(args):
    ensure_dirs()
    pid = _project(args)
    coord = get_coordinator(pid)
    if coord:
        print(f"🎯 {coord} coordinates project '{pid}'")
    else:
        print(f"📭 no coordinator yet for '{pid}' — first agent to send a task becomes coordinator")


def load_agents() -> dict:
    """Read all agent profiles (name -> {strengths, skills, last_seen})."""
    out = {}
    for af in sorted(AGENTS_DIR.glob("*/agent.json")):
        try:
            ad = json.load(open(af))
            out[ad.get("name", af.parent.name)] = ad
        except Exception:
            pass
    return out


def cmd_agents(args):
    ensure_dirs()
    agents = load_agents()
    if not agents:
        print("📭 no agents registered")
        print("💡 register at install: install.sh ... --strengths \"hard reasoning, architecture (GPT-5.5)\"")
        return
    # Descriptive matrix. Routing is NOT a fixed table — the coordinator reads this
    # plus the project's CONTEXT.md and decides who fits THIS project's needs.
    print("Routing is decided per project by the coordinator, not by a fixed map.")
    print("Read each agent's strengths + the project context, then `bridge send --to <agent>`.\n")
    for name, ad in sorted(agents.items()):
        strengths = ad.get("strengths") or "(no strengths registered)"
        print(f"• {name}: {strengths}")
        skills = ad.get("skills") or []
        if skills:
            print(f"    tags: {', '.join(skills)}")


def cmd_inbox(args):
    name = _get_name(args)
    ensure_dirs()
    pid = _project(args)
    # ponytail: auto-fail stale working tasks before checking inbox
    _auto_stale_working(pid, name)
    bp = board_path(pid)
    board = read_board(bp)
    mine = [t for t in board["tasks"] if _inbox_filter(t, name)]
    if not mine:
        print("📭 no pending tasks")
        return
    for t in sorted(mine, key=lambda x: x["created"]):
        print(f"  [{t['id']}] [{t['status']}] {t['subject']} (from {t['from']})")
        # show the content agents need to actually do the work
        if t.get("body"):
            print(f"        ↳ {t['body']}")
        if t.get("files"):
            print(f"        📎 files: {', '.join(t['files'])}")
        if t.get("question"):
            print(f"        ❓ question: {t['question']}")
        if t.get("answer"):
            print(f"        💬 answer: {t['answer']}")
        if t.get("review_comment"):
            print(f"        🔄 review: {t['review_comment']}")


def cmd_show(args):
    name = _get_name(args)
    ensure_dirs()
    board = read_board(board_path(_project(args)))
    t = _find_task(board, args.task_id)
    if not t:
        print(f"❌ task {args.task_id} not found", file=sys.stderr)
        sys.exit(1)
    for k in ("id", "subject", "status", "from", "to", "body", "files",
              "question", "answer", "result", "review_verdict", "review_comment",
              "created", "updated"):
        v = t.get(k)
        if v:
            print(f"{k:14} {', '.join(v) if isinstance(v, list) else v}")


def cmd_claim(args):
    name = _get_name(args)
    ensure_dirs()
    bp = board_path(_project(args))
    def _claim(board):
        for t in board["tasks"]:
            if t["id"] == args.task_id:
                if t["to"] != name:
                    raise SystemExit(f"❌ task {args.task_id} is not assigned to you")
                if t["status"] not in ("pending", "input_required", "changes_requested"):
                    raise SystemExit(f"❌ task {args.task_id} is already {t['status']}")
                t["status"] = "working"
                t["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                return board
        raise SystemExit(f"❌ task {args.task_id} not found")
    try:
        atomic_update_board(bp, _claim)
        print(f"✅ claimed {args.task_id}")
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


def cmd_done(args):
    name = _get_name(args)
    ensure_dirs()
    pid = _project(args)
    bp = board_path(pid)
    subject = ""
    def _done(board):
        nonlocal subject
        for t in board["tasks"]:
            if t["id"] == args.task_id:
                if t["to"] != name:
                    raise SystemExit(f"❌ task {args.task_id} is not assigned to you")
                subject = t["subject"]
                t["status"] = "completed"
                t["result"] = args.result
                t["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                if args.files:
                    t["files"] = args.files.split(",")
                return board
        raise SystemExit(f"❌ task {args.task_id} not found")
    try:
        atomic_update_board(bp, _done)
        # ponytail: auto-log activity after successful write
        append_activity(pid, {
            "agent": name, "action": "done", "task_id": args.task_id,
            "subject": subject, "result": args.result,
        })
        # ponytail: auto-archive if completed tasks exceed threshold
        _auto_archive(pid, name)
        print(f"✅ completed {args.task_id}: {subject}")
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


def cmd_board(args):
    name = _get_name(args)
    ensure_dirs()
    bp = board_path(_project(args))
    board = read_board(bp)
    if not board["tasks"]:
        print("📭 board is empty")
        return
    print(f"{'ID':<14} {'STATUS':<18} {'OWNER':<10} {'SUBJECT'}")
    print("-" * 80)
    for t in sorted(board["tasks"], key=lambda x: x["created"]):
        status = t["status"]
        owner = f"{t['from']}→{t['to']}"
        print(f"{t['id']:<14} {status:<18} {owner:<10} {t['subject']}")


def _find_task(board: dict, task_id: str):
    for t in board["tasks"]:
        if t["id"] == task_id:
            return t
    return None


def _check_owner(task: dict, name: str, task_id: str):
    if task["to"] != name:
        print(f"❌ task {task_id} is not assigned to you", file=sys.stderr)
        sys.exit(1)


def cmd_question(args):
    name = _get_name(args)
    ensure_dirs()
    bp = board_path(_project(args))
    def _q(board):
        for t in board["tasks"]:
            if t["id"] == args.task_id:
                if t["to"] != name:
                    raise SystemExit(f"❌ task {args.task_id} is not assigned to you")
                t["status"] = "input_required"
                t["question"] = args.body
                t["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                return board
        raise SystemExit(f"❌ task {args.task_id} not found")
    try:
        atomic_update_board(bp, _q)
        print(f"❓ question on {args.task_id}: {args.body}")
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


def cmd_answer(args):
    name = _get_name(args)
    ensure_dirs()
    bp = board_path(_project(args))
    def _a(board):
        for t in board["tasks"]:
            if t["id"] == args.task_id:
                if t["from"] != name:
                    raise SystemExit(f"❌ task {args.task_id} was sent by {t['from']}, not you")
                t["status"] = "working"
                t["answer"] = args.body
                t["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                return board
        raise SystemExit(f"❌ task {args.task_id} not found")
    try:
        atomic_update_board(bp, _a)
        print(f"✅ answered {args.task_id}, task unblocked")
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


def cmd_review(args):
    if args.verdict is None:  # review request
        name = _get_name(args)
        ensure_dirs()
        bp = board_path(_project(args))
        def _req(board):
            for t in board["tasks"]:
                if t["id"] == args.task_id:
                    if t["to"] != name:
                        raise SystemExit(f"❌ task {args.task_id} is not assigned to you")
                    t["status"] = "review_requested"
                    t["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    return board
            raise SystemExit(f"❌ task {args.task_id} not found")
        try:
            atomic_update_board(bp, _req)
            rp = bp.parent / "reviews"
            rp.mkdir(exist_ok=True)
            review = {"task_id": args.task_id, "requested_by": name, "requested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "verdict": None}
            rid = _new_task_id()
            with open(rp / f"{rid}.json", "w") as rf:
                json.dump(review, rf, indent=2)
            print(f"👀 review requested on {args.task_id} (review {rid})")
        except SystemExit as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
    else:  # review verdict
        name = _get_name(args)
        ensure_dirs()
        bp = board_path(_project(args))
        verdict = args.verdict
        if verdict not in ("approve", "changes"):
            print("❌ verdict must be 'approve' or 'changes'", file=sys.stderr)
            sys.exit(1)
        def _verdict(board):
            for t in board["tasks"]:
                if t["id"] == args.task_id:
                    t["status"] = "review_approved" if verdict == "approve" else "changes_requested"
                    t["review_verdict"] = verdict
                    if args.body:
                        t["review_comment"] = args.body
                    t["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    return board
            raise SystemExit(f"❌ task {args.task_id} not found")
        try:
            atomic_update_board(bp, _verdict)
            print(f"{'✅' if verdict=='approve' else '🔄'} review {verdict} on {args.task_id}")
        except SystemExit as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)


def cmd_activity(args):
    name = _get_name(args)
    ensure_dirs()
    ap = activity_path(_project(args))
    if not ap.exists():
        print("📭 no activity yet")
        return
    since = args.since
    with open(ap, "r") as f:
        lines = f.readlines()
    for line in lines:
        try:
            entry = json.loads(line.strip())
        except json.JSONDecodeError:
            continue
        if since and entry.get("ts", "") < since:
            continue
        agent = entry.get("agent", "?")
        action = entry.get("action", "?")
        detail = entry.get("subject", entry.get("what", ""))
        ts = entry.get("ts", "")
        print(f"  [{ts}] {agent} {action}: {detail}")


def cmd_log(args):
    name = _get_name(args)
    ensure_dirs()
    append_activity(_project(args), {
        "agent": name, "action": "log", "what": args.what,
    })
    print(f"✅ logged")


def cmd_project(args):
    name = _get_name(args)
    ensure_dirs()
    if args.action == "init":
        # workspace binding = the security boundary. Default to cwd so two agents
        # editing the same repo auto-share this project (and ONLY this project).
        workspace = str(Path(args.workspace).resolve()) if args.workspace else str(Path.cwd().resolve())
        pid = args.name or Path(workspace).name
        pdir = PROJECTS_DIR / pid
        pdir.mkdir(parents=True, exist_ok=True)
        pj = pdir / "project.json"
        if not pj.exists():
            json.dump({"id": pid, "workspace": workspace, "goal": args.goal or ""},
                      open(pj, "w"), indent=2)
        pmd = pdir / "PROJECT.md"
        if not pmd.exists():
            pmd.write_text(f"# {pid}\n\nWorkspace: {workspace}\nGoal: {args.goal or 'N/A'}\n")
            print(f"✅ project {pid} created, bound to {workspace}")
        else:
            print(f"ℹ️  project {pid} already exists (bound to {project_workspace(pid) or workspace})")
    elif args.action == "list":
        projects = [d.name for d in PROJECTS_DIR.iterdir() if d.is_dir()]
        if not projects:
            print("📭 no projects")
        else:
            for p in sorted(projects):
                print(f"  {p}")
    elif args.action == "show":
        pid = args.name or "default"
        pmd = PROJECTS_DIR / pid / "PROJECT.md"
        if pmd.exists():
            print(pmd.read_text())
        else:
            print(f"❌ project {pid} not found", file=sys.stderr)
            sys.exit(1)


def cmd_context(args):
    name = _get_name(args)
    ensure_dirs()
    pid = _project(args)
    pdir = PROJECTS_DIR / pid
    pdir.mkdir(parents=True, exist_ok=True)
    cm = pdir / "CONTEXT.md"
    if args.show:
        if cm.exists():
            print(cm.read_text())
        else:
            print("📭 no context yet")
    elif args.add:
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        entry = f"\n[{ts}] {name}: {args.add}\n"
        with open(cm, "a") as f:
            f.write(entry)
        print(f"✅ context added")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(prog="bridge", description="agent-bridge CLI")
    parser.add_argument("--as", dest="as_", help="agent identity")

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("whoami", help="print current agent identity")
    sub.add_parser("doctor", help="check agent-bridge readiness")

    sp = sub.add_parser("status", help="show inbox summary")
    sp.add_argument("--oneliner", action="store_true", help="single-line output for hooks")

    # send
    sp = sub.add_parser("send", help="send a task to another agent")
    sp.add_argument("--to", help="target agent name (omit for --skill auto-route)")
    sp.add_argument("--skill", help="auto-route to best agent for this skill (coding|review|reasoning|planning|analysis|writing|terminal|build)")
    sp.add_argument("--subject", required=True, help="task subject")
    sp.add_argument("--body", help="task body")
    sp.add_argument("--files", help="comma-separated file paths")
    sp.add_argument("--project", help="project id (default: default)")
    sp.add_argument("--no-wake", action="store_true", help="skip auto-waking the target agent (by default, send always wakes)")

    # wake
    sp = sub.add_parser("wake", help="wake an idle agent to check its inbox (if it registered a headless command)")
    sp.add_argument("agent", help="agent name")

    # show
    sp = sub.add_parser("show", help="show full detail of one task (body, question, answer, result)")
    sp.add_argument("task_id", help="task id")
    sp.add_argument("--project", help="project id")

    # agents
    sp = sub.add_parser("agents", help="show agent capability matrix")

    # who-coordinates
    sp = sub.add_parser("who-coordinates", help="show project coordinator")
    sp.add_argument("--project", help="project id")

    # inbox
    sp = sub.add_parser("inbox", help="list tasks needing my action")
    sp.add_argument("--project", help="project id")

    # claim
    sp = sub.add_parser("claim", help="claim a task")
    sp.add_argument("task_id", help="task id")
    sp.add_argument("--project", help="project id")

    # done
    sp = sub.add_parser("done", help="mark a task as completed")
    sp.add_argument("task_id", help="task id")
    sp.add_argument("--result", required=True, help="result description")
    sp.add_argument("--files", help="comma-separated file paths")
    sp.add_argument("--project", help="project id")

    # board
    sp = sub.add_parser("board", help="show full task board")
    sp.add_argument("--project", help="project id")

    # clean
    sp = sub.add_parser("clean", help="clean up old completed/failed/canceled tasks")
    sp.add_argument("--days", type=int, help="remove tasks older than N days (based on updated time)")
    sp.add_argument("--all", action="store_true", dest="clean_all", help="remove all completed/failed/canceled tasks regardless of age")
    sp.add_argument("--status", help="comma-separated statuses to clean (default: completed,failed,canceled)")
    sp.add_argument("--dry-run", action="store_true", help="preview without deleting")
    sp.add_argument("--project", help="project id")

    # question
    sp = sub.add_parser("question", help="ask a question back (blocks task)")
    sp.add_argument("task_id", help="task id")
    sp.add_argument("--body", required=True, help="question text")
    sp.add_argument("--project", help="project id")

    # answer
    sp = sub.add_parser("answer", help="answer a question (unblocks task)")
    sp.add_argument("task_id", help="task id")
    sp.add_argument("--body", required=True, help="answer text")
    sp.add_argument("--project", help="project id")

    # review
    sp = sub.add_parser("review", help="request or verdict a review")
    sp.add_argument("task_id", help="task id")
    sp.add_argument("--verdict", choices=["approve", "changes"], help="review verdict")
    sp.add_argument("--body", help="review comment")
    sp.add_argument("--project", help="project id")

    # activity
    sp = sub.add_parser("activity", help="show activity feed")
    sp.add_argument("--since", help="ISO timestamp filter")
    sp.add_argument("--project", help="project id")

    # log
    sp = sub.add_parser("log", help="append a manual activity entry")
    sp.add_argument("--what", required=True, help="description")
    sp.add_argument("--project", help="project id")

    # project
    sp = sub.add_parser("project", help="manage projects")
    sp.add_argument("action", choices=["init", "list", "show"], help="action")
    sp.add_argument("--name", help="project id (default: workspace dir name)")
    sp.add_argument("--workspace", help="workspace path to bind (init only; default: cwd)")
    sp.add_argument("--goal", help="project goal (init only)")

    # context
    sp = sub.add_parser("context", help="manage shared context")
    sp.add_argument("--show", action="store_true", help="show context")
    sp.add_argument("--add", help="append context entry")
    sp.add_argument("--project", help="project id")

    # placeholder stubs
    # (none remaining — all stub commands now implemented)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "whoami": cmd_whoami, "doctor": cmd_doctor, "status": cmd_status,
        "send": cmd_send, "inbox": cmd_inbox, "claim": cmd_claim,
        "done": cmd_done, "board": cmd_board, "clean": cmd_clean, "agents": cmd_agents,
        "show": cmd_show, "wake": cmd_wake, "who-coordinates": cmd_who_coordinates,
        "question": cmd_question, "answer": cmd_answer, "review": cmd_review,
        "activity": cmd_activity, "log": cmd_log,
        "project": cmd_project, "context": cmd_context,
    }
    cmd = commands.get(args.command)
    if cmd:
        cmd(args)
    else:
        print(f"command '{args.command}' not yet implemented", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()