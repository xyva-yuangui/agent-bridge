# agent-bridge

**English** | [简体中文](README.zh-CN.md)

agent-bridge gives Codex, Claude Code, Reasonix, and ZCode one local task
board. It uses only Python's standard library and keeps data under
`~/.agent-bridge`.

## Requirements

- Windows 10/11 with PowerShell 5.1+, or macOS/Linux with Bash
- Python 3.9+
- One or more supported agent applications

## Install

Windows PowerShell:

```powershell
.\install.ps1 -Auto
.\install.ps1 -Agent codex -As codex -Python C:\path\to\python.exe
.\install.ps1 -Auto -Uninstall
```

macOS or Linux:

```bash
./install.sh --auto
./install.sh --agent codex --as codex --python /usr/bin/python3
./install.sh --auto --uninstall
```

Both installers are thin, separately-quoted bootstraps: they install the
package and invoke `bridge setup`. The Python lifecycle owns the compatibility
runtime, launcher, detected host integrations, profiles, and receipt-verified
native notification helper. `bridge uninstall` preserves task data unless
`--purge-data` is explicitly requested and names the exact data root first.

The macOS installer and platform-neutral tests are included, but a release
should still run the acceptance commands below on a real macOS host.

## Typical workflow

```text
send -> pending -> claim -> working -> done -> completed
                         -> question -> input_required
input_required -> answer -> pending
working -> request review -> review_requested
review_requested -> approve -> completed
review_requested -> changes -> changes_requested -> claim -> working
```

Only the assignee can claim, ask, request review, or finish a task. Only the
original sender can answer questions and issue review verdicts.

```bash
bridge send --to reasonix --subject "Review the patch" --body "Run the tests"
bridge inbox
bridge show <task-id>
bridge claim <task-id>
bridge question <task-id> --body "Which compatibility target?"
bridge answer <task-id> --body "Python 3.9+"
bridge review <task-id>
bridge review <task-id> --verdict approve --body "Accepted"
bridge done <task-id> --result "Implemented and tested"
```

## Delivery semantics

Every notification attempt is observable in `task.delivery.status`:

Compatibility wording: `wake_launched` means `launch_started`,
`acknowledged` means `agent_acknowledged`, and `unavailable` is a degraded
capability rather than a delivery claim.

- `queued`: stored and waiting for a delivery attempt.
- `dispatching`: a dispatcher currently owns the attempt.
- `os_posted` / `plugin_delivered`: a delivery channel accepted the notification;
  the task is not acknowledged yet.
- `viewed`: the target opened the delivery surface.
- `launch_started`: a wake process started; it is not proof the agent read it.
- `agent_acknowledged`: the target checked `status` or `inbox`.
- `claimed`: the assignee claimed the task.
- `retry_wait`: a failed attempt is scheduled for retry.
- `failed`: the delivery attempt itself failed.

`bridge send` never reports a launched process as an acknowledgment. Unknown
targets are rejected when agent profiles exist. On Windows, notifications use
the built-in tray API and do not require BurntToast.

## Core commands

```text
bridge status [--oneliner]       bridge inbox
bridge send --to NAME --subject TEXT [--body TEXT]
bridge claim ID                  bridge done ID --result TEXT
bridge show ID                   bridge board
bridge question ID --body TEXT   bridge answer ID --body TEXT
bridge review ID [--verdict approve|changes] [--body TEXT]
bridge agents                    bridge activity
bridge project init|list|show    bridge context --show|--add TEXT
bridge clean --days N|--all      bridge doctor [--strict]
```

The MCP server exposes the same non-interactive workflows.

## Troubleshooting

Run:

```bash
bridge doctor --strict
bridge status --oneliner
bridge agents
```

If a task remains `launch_started`, the application was started but has not
checked in yet. Restart the target application, verify its hook/MCP config, and
run `bridge inbox`. Configuration paths are native to the host OS, so Windows
configs never depend on `/c/...` paths.

## Test

From the repository root:

```bash
python -m unittest discover -s tests -v
python -m compileall -q scripts tests
```

Windows runtime coverage includes isolated install/reinstall/uninstall,
dependency-free system notification, GBK output, MCP calls, state transitions,
and 40-process concurrent writes. The Windows source installer uses the tracked,
hash-gated native helper in `native/windows-notify/dist`, copies the Python
package into its owned runtime, and removes only receipt-verified native files
during uninstall. On macOS, run the same commands plus an
isolated `./install.sh --auto --install-root <temp-dir>` smoke test.
