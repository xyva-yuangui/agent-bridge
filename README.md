# agent-bridge

**English** | [简体中文](README.zh-CN.md)

<p align="center">
  <img src="https://img.shields.io/badge/version-1.3.0-blue" alt="version">
  <img src="https://img.shields.io/badge/python-3.9+-green" alt="python">
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey" alt="platform">
  <img src="https://img.shields.io/badge/license-MIT-brightgreen" alt="license">
  <img src="https://img.shields.io/badge/dependencies-zero-success" alt="dependencies">
</p>

**Make your AI coding agents work as a team. Local. Zero config. No cloud.**

You have Codex, Claude Code, Reasonix, ZCode — but they don't talk to each other. **agent-bridge** gives them a shared task board on your machine. Delegate work, ask questions, review code — without leaving your terminal. One command to install, zero dependencies, nothing leaves your machine.

> The CLI is `bridge`. Everything lives in `~/.agent-bridge/`. That's it.

---

## Why agent-bridge?

| | Without | With agent-bridge |
|---|---|---|
| **Task handoff** | Copy-paste between terminals | `bridge send --to codex "Design auth"` |
| **Status tracking** | "Did you finish that?" | `bridge board` — see everything at a glance |
| **Code review** | Slack, PR, context switching | `bridge review <id> --verdict approve` |
| **Context sharing** | Scattered across chats | `bridge context --add "decided on JWT"` |
| **Maintenance** | Manual cleanup | Auto-cleans stale tasks, archives old ones |

---

## Quick start

```bash
# 1. Install — auto-detect all agents on this machine
# Windows:
.\install.ps1 -Auto

# macOS / Linux:
./install.sh --auto

# 2. Send a task (auto-wakes the target)
bridge send --to codex --subject "Design the auth module" --body "JWT + refresh"

# 3. The other agent picks it up
bridge inbox            # see what's assigned to me
bridge claim <id>       # I'm on it
bridge done <id> --result "see auth/design.md"

bridge board            # everyone's tasks at a glance
```

The whole loop: **send → claim → done**. Everything else is extra.

---

## Requirements

- **Windows 10/11** with PowerShell 5.1+, or **macOS / Linux** with Bash
- **Python 3.9+** (standard library only — zero pip installs)
- One or more supported agent applications

## Supported agents

| Agent | Desktop | CLI | How it checks in |
|---|:---:|:---:|---|
| **Codex** | ✅ | ✅ | AGENTS.md directive + MCP |
| **Claude Code** | ✅ | ✅ | UserPromptSubmit hook (automatic) |
| **ZCode** | ✅ | — | Plugin hook (automatic) |
| **Reasonix** | ✅ | ✅ | system_prompt + MCP, or `--wake` |

---

## Key features

### Cross-platform file locking
Uses `fcntl.flock` on Unix, `O_CREAT|O_EXCL` portable locks on Windows. Stale lock detection with PID validation prevents deadlocks. 40-process concurrent writes tested and verified.

### Smart routing
Each agent advertises its strengths (`bridge agents`). The coordinator reads the team profile and decides who gets what — no rigid routing tables. Use `--skill` as a routing hint.

### Auto-push: send wakes the target
Every `bridge send` auto-wakes the target agent. Desktop notification + headless execution — no manual terminal switching. Use `--no-wake` for non-urgent tasks.

### Delivery tracking
Every notification attempt is observable in `task.delivery.status`:

| Status | Meaning |
|---|---|
| `queued` | Stored, waiting for delivery attempt |
| `wake_launched` | Wake process started (not proof of receipt) |
| `acknowledged` | Target checked in via `status`, `inbox`, or `claim` |
| `unavailable` | No usable notification or wake channel |
| `failed` | Delivery attempt itself failed |

`bridge send` never reports a launched process as an acknowledgment.

### Auto-cleanup
The board maintains itself: completed tasks older than 7 days are silently cleaned, working tasks stuck >24h are auto-failed, and overflow completed tasks are archived to `archive.json`.

### 100% local, 100% private
No cloud, no server, no account. Everything lives in `~/.agent-bridge/`. One machine = one team. Sync via Syncthing, Dropbox, or git for multi-machine.

---

## Install

**Windows (PowerShell):**
```powershell
.\install.ps1 -Auto
.\install.ps1 -Agent codex -As codex
.\install.ps1 -Auto -Uninstall
```

**macOS / Linux:**
```bash
./install.sh --auto
./install.sh --agent codex --as codex
./install.sh --auto --uninstall
```

Both installers are idempotent. Restart agent applications after install to load MCP and hook configuration.

---

## Core commands

```text
bridge status [--oneliner]         bridge inbox
bridge send --to NAME --subject TEXT [--body TEXT] [--no-wake] [--skill TAG]
bridge claim ID                    bridge done ID --result TEXT
bridge show ID                     bridge board
bridge question ID --body TEXT     bridge answer ID --body TEXT
bridge review ID [--verdict approve|changes] [--body TEXT]
bridge agents                      bridge activity [--since TS]
bridge project init|list|show      bridge context --show|--add TEXT
bridge clean --days N|--all [--dry-run]  bridge doctor [--strict]
bridge whoami                      bridge wake AGENT
bridge who-coordinates             bridge log --what TEXT
```

The MCP server exposes the same 20 workflows as MCP tools (`bridge_send`, `bridge_inbox`, …).

### Task lifecycle

```
send → pending → claim → working → done → completed
                          ↘ question → input_required → answer → pending
                          ↘ review → review_requested → approve → completed
                                                   ↘ changes → changes_requested → claim → working
```

Only the assignee can claim, ask, review, or finish. Only the sender can answer questions and issue verdicts.

---

## Troubleshooting

```bash
bridge doctor --strict       # full health check
bridge status --oneliner     # quick inbox count
bridge agents                # see who's available
```

If a task stays `wake_launched`, the agent started but hasn't checked in. Restart the target application and verify its hook/MCP config.

---

## Test

```bash
python -m unittest discover -s tests -v    # 29 tests
python -m compileall -q scripts tests      # syntax check
```

Windows coverage includes isolated install/reinstall/uninstall, dependency-free notification, GBK output, MCP calls, and 40-process concurrent writes.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
