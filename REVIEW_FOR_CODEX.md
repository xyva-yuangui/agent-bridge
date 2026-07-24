# Code Review Request: agent-bridge v1.2.0

## What to review

The latest commit `e5e0892` in `/tmp/roundtable` (repo: `xyva-yuangui/agent-bridge`).

```bash
cd /tmp/roundtable
git log --oneline -3
git diff HEAD~1
```

## Changes

### 1. `scripts/bridge.py` — cross-platform fcntl replacement
- Replaced `import fcntl` with `try/except ImportError` + portable fallback
- New `_locked_file()` context manager: uses `fcntl.flock` on Unix, `os.O_CREAT|O_EXCL` on Windows
- All 6 file-locking sites updated to use `_locked_file()`

### 2. `scripts/bridge.py` — _wake_agent fixes
- Now passes `AGENT_BRIDGE_NAME=<target>` to child process
- Cross-platform `start_new_session` / `creationflags`
- Enhanced prompt: "Claim ALL pending tasks, keep going until inbox empty"

### 3. `scripts/bridge.py` — other fixes
- `_desktop_notify`: added Windows `msg.exe` notification
- `_under`: case-insensitive path comparison for Windows

### 4. `install.sh` — Claude wake registration
- Added `claude -p` as wake command for Claude Code

### 5. `SKILL.md` — collaboration instructions
- Added "Continuous collaboration" section
- Added "Coordinator mode" section

## Focus areas

1. Does the portable lock correctly handle concurrent access?
2. Is `_maybe_rotate(ap)` properly called in `append_activity`?
3. Any edge cases in the `_locked_file` context manager?
4. Does the wake prompt correctly instruct agents to process ALL tasks?

## How to submit review

After reviewing, use `bridge done`:
```bash
AGENT_BRIDGE_NAME=codex /tmp/roundtable/scripts/bridge.py claim <task-id>
AGENT_BRIDGE_NAME=codex /tmp/roundtable/scripts/bridge.py done <task-id> --result "your review findings"
```

Your task ID: check with `bridge inbox`
