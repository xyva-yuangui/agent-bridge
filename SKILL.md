---
name: agent-bridge
description: Cross-agent collaboration: run bridge status every turn; if pending tasks exist, handle them before responding.
---

# agent-bridge — cross-agent collaboration

agent-bridge lets multiple AI coding agents (ZCode, Reasonix, Claude Code, Codex) on the same machine collaborate like teammates. Delegate tasks, share a board, see activity, review code.

## Quick start

```bash
# Install
install.sh --agent zcode --as zcode

# Check status (every turn start)
bridge status --oneliner

# See your inbox
bridge inbox

# Send a task (auto-wakes target by default)
bridge send --to reasonix --subject "Review PR #42" --body "Please review the changes"

# Claim and complete
bridge claim <task-id>
bridge done <task-id> --result "Reviewed, LGTM" --files src/main.py
```

## Commands

| Command | Description |
|---|---|
| `bridge whoami` | Print current agent identity |
| `bridge doctor` | Check agent-bridge readiness |
| `bridge status` | Show inbox summary (use `--oneliner` for hooks) |
| `bridge send --to <agent> --subject "..."` | Send a task (auto-wakes target by default) |
| `bridge send --skill coding --subject "..."` | Auto-route to best agent for skill |
| `bridge inbox` | List tasks needing your action (shows subject + body + any question/answer) |
| `bridge show <task-id>` | Full detail of one task — read this before working |
| `bridge claim <task-id>` | Claim a task (pending→working) |
| `bridge done <task-id> --result "..."` | Complete a task |
| `bridge question <task-id> --body "..."` | Ask a question back (blocks task) |
| `bridge answer <task-id> --body "..."` | Answer a question (unblocks) |
| `bridge review <task-id>` | Request a review |
| `bridge review <task-id> --verdict approve\|changes` | Issue review verdict |
| `bridge board` | Show full task board |
| `bridge clean --all` | Clean up completed/failed/canceled tasks |
| `bridge clean --days 7` | Clean tasks older than N days |
| `bridge clean --dry-run` | Preview what would be cleaned |
| `bridge agents` | Show agent capability matrix |
| `bridge who-coordinates` | Show project coordinator |
| `bridge activity [--since <ts>]` | Show activity feed |
| `bridge log --what "..."` | Append manual activity entry |
| `bridge project init\|list\|show` | Manage projects |
| `bridge context --add\|--show` | Shared context/decisions |

## Coordinator model

The first agent to send a task in a project becomes the **coordinator**. The coordinator's model decides routing by reading `bridge agents` and using its own judgment — not static rules.

```bash
# First agent to send → becomes coordinator
bridge send --to reasonix --subject "Design architecture"

# Coordinator sees capability matrix, model decides routing
bridge agents
bridge send --to codex --subject "Implement API"  # model judged codex is best
```

## Task lifecycle

```
pending → accepted → working → completed/failed/canceled
                 ↘ input_required (question) → answered
                 ↘ review_requested → review_approved/changes_requested
```

## Auto-push (send → wake)

Every `bridge send` automatically wakes the target agent (if it has a registered wake command). No manual terminal switching needed.

```bash
# Default: auto-wake
bridge send --to reasonix --subject "Urgent review"

# Opt-out: silent send
bridge send --to reasonix --subject "Non-urgent" --no-wake
```

## Auto-cleanup (transparent maintenance)

The board is automatically kept clean with zero manual effort:

| Mechanism | Trigger | Rule |
|---|---|---|
| Silent auto-clean | Every `bridge status` call | Completed tasks older than 7 days (when ≥10 tasks on board) |
| Stale task detection | Every `bridge status` call | Working tasks stuck >24h → auto-failed |
| Overflow archive | After `bridge done` | Completed tasks >50 → oldest half archived |

All cleaned tasks are archived to `archive.json` — never lost, just out of the way.

## Inbox rules

Your inbox shows tasks where:
- You are the **assignee** (`to` = you) and status is `pending` or `changes_requested`
- You are the **original sender** (`from` = you) and status is `input_required` (someone asked you a question) or `review_requested` (someone asked you to review their work)

Tasks older than 30 days are automatically excluded from the inbox to prevent zombie history.

## Two ways to call bridge

- **CLI / hooks** (Claude Code, Reasonix, Codex terminals): run `bridge <cmd>` via Bash.
- **MCP tools** (desktop apps + any MCP client): same actions exposed as `bridge_status`, `bridge_inbox`, `bridge_send`, `bridge_claim`, `bridge_done`, `bridge_review`, etc. Call `bridge_status` at the start of every turn.

## Troubleshooting

Run `bridge doctor` to check:
- Identity and directory permissions
- Board version compatibility
- Agent heartbeat freshness
- Skill path and hook configuration