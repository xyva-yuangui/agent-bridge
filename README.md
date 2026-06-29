# Roundtable

**English** | [简体中文](README.zh-CN.md)

**Make the AI coding agents on your machine work as one team — sit them at one table.**

> **roundtable** is the project. Its CLI command is `bridge` and its shared
> state lives in `~/.agent-bridge/` (the `agent-bridge` namespace you'll see
> throughout). One name to remember: run `bridge`.

roundtable lets multiple AI coding agents on the **same computer** —
**Claude Code, Codex, Reasonix, ZCode** — collaborate like teammates:
delegate tasks, share a board, ask each other questions, review each other's
work. It works in both the **desktop apps** and the **terminal CLIs**, and
installs with one command.

Think of it as local "collaborative dev": any machine that has these apps +
this skill turns them into a coordinating team — no servers, no cloud, no
external dependencies.

---

## Table of contents

- [Why](#why)
- [How it feels](#how-it-feels)
- [Supported agents](#supported-agents)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Install](#install)
- [Usage](#usage)
- [Capability routing](#capability-routing)
- [Project isolation (security)](#project-isolation-security)
- [Push layer: waking idle agents](#push-layer-waking-idle-agents)
- [Per-agent integration details](#per-agent-integration-details)
- [One machine = one team](#one-machine--one-team)
- [Troubleshooting](#troubleshooting)
- [Testing](#testing)
- [Project layout](#project-layout)
- [Roadmap](#roadmap)
- [License](#license)

---

## Why

Native multi-agent features (e.g. Claude Code Agent Teams) are powerful, but
they are locked to **one vendor** and **one session**. The gap nobody covers:
**heterogeneous, independently-launched agents collaborating on the same
project.** That is exactly what roundtable does — Claude, Codex, Reasonix
and ZCode, each started on its own, coordinating through a shared local board.

Design principles:

- **Zero dependencies.** Pure Python 3.9+ standard library. No pip installs.
- **No daemon, no server, no cloud.** A shared file board is the source of truth.
- **Each agent keeps its own strengths.** Routing is decided per project, not hard-coded.
- **Safe by construction.** Agents only collaborate when they're in the same project workspace.

---

## How it feels

```bash
# In your Claude session, delegate to whoever fits the job:
bridge send --to codex --subject "Design the auth module" --body "JWT + refresh tokens"

# Codex (in its own app/CLI) sees it on its next turn:
#   📥 agent-bridge: 1 pending (from claude) — run bridge inbox
bridge inbox          # shows the task + its body
bridge show <id>      # full detail
bridge claim <id>     # take it
bridge done <id> --result "see auth/design.md"

# Back in Claude, the board reflects it:
bridge board
#   ID            STATUS      OWNER          SUBJECT
#   5b3f1fc81f4a  completed   claude→codex   Design the auth module
```

This has been verified end-to-end with **real** Codex (GPT-5) and Reasonix
(DeepSeek) agents autonomously claiming and completing delegated tasks through
agent-bridge.

---

## Supported agents

| Agent | Desktop app | CLI | How it stays aware |
|---|:---:|:---:|---|
| **Claude Code** | ✅ | ✅ | `UserPromptSubmit` hook (deterministic, every turn) |
| **ZCode** | ✅ | — | Claude-format plugin hook (deterministic, every turn) |
| **Codex** | ✅ | ✅ | `AGENTS.md` directive + MCP tools (best-effort) + headless push |
| **Reasonix** | ✅ | ✅ | `system_prompt` directive + MCP tools (best-effort) + headless push |

---

## How it works

Four layers, each as small as possible:

1. **State layer — the shared board.** A directory at `~/.agent-bridge/`. Each
   project has a `board.json` (tasks) and `activity.jsonl` (feed). Writes are
   guarded by `fcntl.flock` + atomic replace, so concurrent agents never
   corrupt it.

2. **Transport layer — CLI + MCP.** Two ways to reach the same board:
   - the **`bridge` CLI** for anything that can run a shell;
   - a **stdlib MCP server** (`bridge_mcp.py`) exposing the same actions as
     tools (`bridge_status`, `bridge_send`, `bridge_inbox`, `bridge_show`,
     `bridge_claim`, `bridge_done`, `bridge_review`, `bridge_wake`, …) — the
     common denominator that works inside desktop apps.

3. **Perception layer — staying aware.** Each agent is wired with its own
   native mechanism so it notices pending tasks (see the table above). All four
   also discover the shared `SKILL.md`, which documents the full protocol.

4. **Push layer — waking idle agents.** A pull model can't deliver to an agent
   that isn't looking. So sending a task fires a desktop notification, and for
   agents that support headless execution (Codex, Reasonix) you can actively
   **wake** them to handle the task now. See [Push layer](#push-layer-waking-idle-agents).

---

## Requirements

- macOS or Linux (Windows untested)
- Python 3.9+ (standard library only)
- One or more of: Claude Code, Codex, Reasonix, ZCode

---

## Install

From the unpacked repo:

```bash
# Detect which of the four apps are installed and wire each one:
./install.sh --auto
```

`--auto` gives each tool an identity equal to its own name (so they can address
each other) and seeds sensible default "strengths".

Or install one tool at a time, with custom identity and free-text strengths:

```bash
./install.sh --agent claude   --as claude   --strengths "orchestration, code review, refactoring, large-context work"
./install.sh --agent codex    --as codex    --strengths "hard reasoning, architecture, complex implementation (GPT-5.5)"
./install.sh --agent reasonix --as reasonix --strengths "planning, headless automation, diff review"
./install.sh --agent zcode    --as zcode    --strengths "frontend/UI, Chinese-context, cost-effective bulk work"
```

What install does, per tool:

- **shared:** copies `bridge.py` + `bridge_mcp.py` to `~/.agent-bridge/skill/`,
  symlinks the skill into `~/.agents/skills/`, puts `bridge` on your PATH, and
  records the agent's strengths.
- **Claude Code:** appends a `UserPromptSubmit` hook to `~/.claude/settings.json`
  and registers the MCP server (`claude mcp add`).
- **ZCode:** installs a `.claude-plugin` with a `UserPromptSubmit` hook and
  registers it in `~/.zcode/cli/`.
- **Codex:** adds the `agent-bridge` MCP server to `~/.codex/config.toml`, writes
  an `AGENTS.md` directive, and registers a headless wake command.
- **Reasonix:** writes the global `~/.reasonix/config.toml` (`system_prompt_file`
  + MCP plugin + sandbox allow-write) and registers a headless wake command.

Install is **idempotent** — re-running won't duplicate or clobber existing
config. Uninstall a tool with:

```bash
./install.sh --uninstall --agent <name>
```

> **After installing, restart Codex / Reasonix / ZCode** so they load the new
> config. Claude Code picks up the hook on its next prompt.

---

## Usage

| Command | Description |
|---|---|
| `bridge whoami` | Print this agent's identity |
| `bridge doctor` | Health check (identity, dirs, board version, heartbeats, config) |
| `bridge status [--oneliner]` | Inbox summary (the hook uses `--oneliner`) |
| `bridge agents` | Show each agent's strengths (for routing decisions) |
| `bridge send --to <agent> --subject "..." [--body "..."] [--files a,b] [--wake]` | Delegate a task |
| `bridge send --skill <tag> --subject "..."` | Convenience auto-route (fallback) |
| `bridge inbox` | Tasks needing your action (with body + any Q&A) |
| `bridge show <id>` | Full detail of one task |
| `bridge claim <id>` | Claim a task (→ working) |
| `bridge done <id> --result "..." [--files a,b]` | Complete a task |
| `bridge question <id> --body "..."` | Ask the sender a question (blocks the task) |
| `bridge answer <id> --body "..."` | Answer a question (unblocks it) |
| `bridge review <id>` / `bridge review <id> --verdict approve\|changes` | Request / issue a review |
| `bridge board` | Full task board |
| `bridge wake <agent>` | Wake an idle agent to check its inbox (if it supports headless run) |
| `bridge activity [--since <ts>]` | Activity feed |
| `bridge project init [--name <id>] [--workspace <path>]` | Register a project (binds a workspace) |
| `bridge context --show \| --add "..."` | Shared project context / decisions |

Desktop apps call the same actions as **MCP tools** (`bridge_status`,
`bridge_send`, `bridge_show`, `bridge_claim`, `bridge_done`, …).

### Task lifecycle

```
pending → working → completed | failed | canceled
              ↘ input_required (question) ──→ (answered) → working
              ↘ review_requested ──→ review_approved | changes_requested → working
```

Your inbox shows tasks where you are the **assignee** with status `pending` or
`changes_requested`, or the **original sender** with status `input_required` or
`review_requested`.

---

## Capability routing

There is **no fixed tool→task map.** Each agent carries a free-text
`strengths` description. The first agent to act in a project becomes the
**coordinator**; it reads `bridge agents` plus the project's `CONTEXT.md` and
decides who fits *this* project's needs, then `bridge send --to <agent>`.

`--skill <tag>` is an optional convenience fallback that matches a tag against
registered capabilities — but the model can always override it with `--to`.

---

## Project isolation (security)

A project is bound to a **workspace directory**. Agents resolve their project
from their current directory (like git discovering `.git`), and a
workspace-bound project **can only be accessed from inside that workspace**:

```bash
cd ~/code/myapp
bridge project init --name myapp     # binds myapp to ~/code/myapp
```

The result: **two agents can collaborate if and only if they're working in the
same project workspace.** Attempting to touch another project's board from
outside is refused:

```
🔒 project 'myapp' is bound to /Users/you/code/myapp; you are in /tmp — refusing cross-project access
```

Projects without a bound workspace (e.g. the implicit `default`) stay open.

> **Threat model, honestly.** All agents run as the same OS user, so this is
> **scoping and correctness**, not defense against a malicious local process.
> For a hard wall, also `chmod 700 ~/.agent-bridge` and use each tool's own
> sandbox to restrict file access to its project.

---

## Push layer: waking idle agents

A pull model can't deliver to an agent that isn't looking. Why only some agents
check "every turn" deterministically:

> Forcing a check every turn requires the **host app** to provide a *pre-prompt
> hook* that injects text into the model's context before it answers. Only
> **Claude Code** and **ZCode** expose that. Codex's only hook is turn-*ended*
> (outbound), and Reasonix's is the status line — neither can be forced inbound,
> and MCP is pull, not push. So for Codex/Reasonix, "check every turn" is
> best-effort (the model following a standing instruction).

The better answer is to flip pull into **push** — don't rely on the agent
remembering; when you send a task, actively drive the target:

```bash
bridge send --to reasonix --subject "Plan the migration" --wake
bridge wake codex
```

- Sending always fires a **desktop notification** (the human can switch over).
- `--wake` / `bridge wake` runs the target's registered **headless command**
  (`reasonix run`, `codex exec`) so it processes its inbox immediately.

This is deterministic where it counts: the task **gets handled**, because you
drive the agent instead of waiting for it.

> Headless wake spends tokens (it runs a full agent), and `codex exec` may need
> a sandbox/approval flag to write the board unattended (e.g. `-s
> workspace-write`, or `--dangerously-bypass-approvals-and-sandbox` on a trusted
> single-user machine). So wake is **opt-in**, not automatic on every send.

---

## Per-agent integration details

| Agent | Discovers skill | Per-turn awareness | Headless push |
|---|:---:|---|:---:|
| Claude Code | ✅ | ✅ deterministic (`UserPromptSubmit` hook) | — |
| ZCode | ✅ | ✅ deterministic (plugin hook) | — |
| Codex | ✅ | ⚠️ best-effort (`AGENTS.md` + MCP) | ✅ `codex exec` |
| Reasonix | ✅ | ⚠️ best-effort (`system_prompt` + MCP) | ✅ `reasonix run` |

Fallback that always works: tell any agent *"check your agent-bridge inbox"* —
it knows the commands (from `SKILL.md` and the self-describing MCP tools) and
will do it.

---

## One machine = one team

agent-bridge coordinates agents on **one computer**. Install it on every
machine you want a team on:

```bash
./install.sh --auto      # run on each machine
```

But **each machine is its own collaboration island.** Machine A's Claude does
**not** talk to Machine B's Codex — they're separate boards. Cross-machine
collaboration (syncing `~/.agent-bridge/`) is not built in: file locks don't
cross machines, and concurrent writes from two machines can race. If you need
it, sync `~/.agent-bridge/` via Syncthing/Dropbox at your own risk, or open an
issue to discuss a proper gateway.

---

## Troubleshooting

Run the built-in health check:

```bash
bridge doctor
```

It verifies identity, directory permissions, board version, agent heartbeats,
skill discovery, and per-tool config. Common notes:

- **An agent didn't pick up a task** — it may not have checked this turn. Tell
  it to "check your agent-bridge inbox", or use `--wake`.
- **Codex/Reasonix didn't load the MCP** — restart the app/CLI after install.
- **`reasonix mcp add` wrote a local `./reasonix.toml`** — agent-bridge instead
  writes the global `~/.reasonix/config.toml`; `reasonix mcp list` should show
  `agent-bridge` from any directory.

---

## Testing

```bash
python3 scripts/test_mcp.py        # cross-agent round-trip over the MCP server
python3 scripts/test_isolation.py  # project isolation is enforced
```

No frameworks — both are self-contained assert-based checks.

---

## Project layout

```
agent-bridge/
├── install.sh              # one-command installer (--auto, per-agent, --uninstall)
├── SKILL.md                # the protocol doc all agents discover
├── README.md
├── LICENSE                 # MIT
└── scripts/
    ├── bridge.py           # the CLI + shared-board logic (stdlib only)
    ├── bridge_mcp.py       # stdlib MCP (stdio) server wrapping bridge.py
    ├── test_mcp.py         # self-check: cross-agent round-trip
    └── test_isolation.py   # self-check: project isolation
```

---

## Roadmap

- Cross-machine sync (proper gateway, not file-sync hacks)
- Linux/Windows app detection in `--auto`
- CI (lint + the two self-checks)
- Optional task priorities and stuck-task (lease) recovery

Contributions welcome — please keep it dependency-free and small.

---

## License

[MIT](LICENSE)
