# agent-bridge Cross-Platform Reliability Design

Date: 2026-07-23

## Goal

Make agent-bridge installable and reliable on Windows and macOS, with one canonical codebase, observable task delivery, safe concurrent state updates, working application integrations, and regression coverage for every issue in the Codex review.

## Scope

The release must:

- Support Python 3.9 or newer using only the standard library.
- Provide a native PowerShell installer for Windows and a Bash installer for macOS/Linux.
- Install one canonical copy of the CLI, MCP server, notification helper, skill, and launchers.
- Configure Codex, Claude Code, Reasonix, and ZCode when detected.
- Keep all task-board data local under `~/.agent-bridge`.
- Preserve the existing CLI and JSON board format where compatibility is practical.
- Make delivery state observable; process launch alone must never be reported as successful delivery.
- Avoid task loss in question/answer, review, stale cleanup, archive, and concurrent-write flows.
- Run safely in Windows legacy code pages without `UnicodeEncodeError`.
- Include standard-library automated tests, with no external test dependency.

The release will not add a network daemon, cloud synchronization, or multi-machine locking.

## Canonical Project Layout

```text
roundtable/
  README.md
  README.zh-CN.md
  SKILL.md
  install.sh
  install.ps1
  scripts/
    bridge.py
    bridge_mcp.py
    notify_windows.ps1
  tests/
    test_bridge.py
    test_cli.py
    test_concurrency.py
    test_mcp.py
    test_installers.py
  docs/superpowers/specs/
  docs/superpowers/plans/
```

Installers copy the complete `scripts/` directory to
`~/.agent-bridge/skill/scripts/`. Skill discovery paths point to that
canonical directory. No second independent copy of `bridge.py` is allowed.

## Core State and Locking

`bridge.py` retains a JSON board per project. All reads and read-modify-write
operations use `_locked_file`.

On POSIX, locking uses `fcntl`. On Windows, a portable lock file contains the
owner PID and timestamp. Acquisition removes a stale lock only when its owner
is gone and its age exceeds the configured stale-lock threshold. Timeout and
permission errors remain distinguishable.

Activity and archive writes use the same locking discipline. Activity rotation
is called after every append. Automatic cleanup archives removed tasks before
they disappear from the board.

## Task State Machine

Legal flows:

```text
pending -> working -> completed|failed|canceled
working -> input_required -> pending
working -> review_requested -> completed|changes_requested
changes_requested -> working
working -> failed (stale timeout)
```

Rules:

- Only the assignee may claim, ask, request review, or complete.
- Only the original sender may answer or issue the requested review verdict.
- Answering returns the task to `pending`, marks a new delivery attempt, and
  notifies/wakes the assignee.
- Approving a review completes the task while preserving review metadata.
- Requesting changes returns it to `changes_requested` and notifies the
  assignee.
- `input_required` is not claimable by the assignee.
- Every transition is validated centrally and timestamped.

## Delivery and Wake Semantics

Each task has a `delivery` object:

```json
{
  "status": "queued|wake_launched|acknowledged|unavailable|failed",
  "attempted_at": "ISO-8601 UTC",
  "acknowledged_at": "ISO-8601 UTC",
  "detail": "human-readable status"
}
```

Agent profiles store `wake_argv` as a JSON array. Legacy string `wake` remains
readable but is never parsed with plain whitespace splitting.

`send`, `answer`, review changes, and questions create a delivery attempt.
Starting a wake process records `wake_launched` and prints “awaiting
acknowledgment.” When the target calls `status` or `inbox`, pending work is
atomically marked `acknowledged`. Claiming also proves acknowledgment.

Unregistered targets are rejected when agent profiles exist. This prevents
typos from creating silent orphan tasks.

## Notifications

Notification functions return an explicit success/failure result.

- Windows runs `notify_windows.ps1` with separate argv parameters. The helper
  uses the standard `System.Windows.Forms.NotifyIcon` balloon API, avoiding the
  optional BurntToast module and PowerShell command interpolation.
- macOS uses `osascript` with JSON-escaped AppleScript strings.
- Linux uses `notify-send`.

Nonzero exit codes produce a visible warning and are recorded in delivery
detail. Notification failure does not roll back task creation.

## Encoding

CLI and MCP output must not crash on GBK or other legacy code pages.
`bridge.py` configures stdout/stderr with `errors="replace"` and uses concise
ASCII status prefixes. MCP subprocesses explicitly use UTF-8 for transport.

## Installation and App Integration

### Windows

`install.ps1`:

- Resolves an explicit Python path or a working Python 3.9+ executable.
- Copies the whole skill and scripts tree atomically.
- Creates `bridge.cmd` using the resolved absolute Python and script paths.
- Adds the launcher directory to the user PATH without duplicating entries.
- Registers agent profiles and detected wake argv values.
- Writes native Windows paths to integrations.
- Configures:
  - Codex MCP server and global `AGENTS.md` directive.
  - Reasonix MCP plugin, system prompt, and sandbox write path.
  - Claude Code `UserPromptSubmit` hook.
  - ZCode `UserPromptSubmit` hook.
- Supports `-Auto`, per-agent installation, explicit identity, explicit
  Python, explicit wake argv, and uninstall.
- Runs `bridge doctor --strict` and fails installation if the integration is
  not runnable.

### macOS/Linux

`install.sh` provides the equivalent `--auto`, per-agent, `--python`,
`--wake-cmd`, and uninstall flows, copies the entire scripts directory, creates
the launcher, registers integrations with POSIX paths, and runs strict doctor.

## Routing and Coordination

Installers populate both `strengths` and `skills`. Skill routing:

- Requires an exact skill tag.
- Prefers agents with recent heartbeats.
- Uses deterministic name ordering only as a tie-breaker.

The coordinator is replaced when the recorded agent no longer exists or has a
stale heartbeat. Coordinator metadata includes an update timestamp.

## MCP Parity

`bridge_mcp.py` exposes every non-interactive CLI capability:

- status, inbox, send, claim, done, show, board, question, answer, review,
  wake, agents, activity, context, clean, doctor, project, whoami,
  who-coordinates, and log.

The reported server version matches the CLI release version. Child processes
receive UTF-8 and identity environment configuration.

## Testing

All tests use `unittest` and temporary directories.

Required coverage:

- Legal and illegal state transitions.
- Question/answer resumes assignee work.
- Review approval completion and review change return.
- Stale working task failure.
- Activity locking and rotation.
- Automatic cleanup archives before removal.
- `clean` requires an explicit scope.
- Delivery acknowledgment and unregistered-target rejection.
- Wake argv handling, including executable paths with spaces.
- Notification argv safety and visible failure reporting.
- GBK-compatible CLI execution.
- MCP initialization, full tool list, and tool calls.
- Concurrent send stress without JSON corruption or lost tasks.
- Static installer assertions for complete file copying, `--auto`, native
  paths, UTF-8, MCP registration, and wake registration.

Windows runtime tests must pass locally. macOS-specific execution is covered by
platform-neutral unit tests and installer static checks here, then requires
ZCode or CI execution on an actual macOS host before a macOS release claim.

## Acceptance Criteria

- Fresh Windows installation completes with strict doctor success.
- `bridge` runs from PowerShell without `PYTHONUTF8` and without encoding
  crashes.
- Windows notification helper exits zero without BurntToast.
- A send/ack/claim/done round trip is observable.
- Question/answer and review flows return work to the correct agent.
- Concurrent stress preserves valid JSON and every task.
- All automated tests pass.
- Installed hashes match the canonical source.
- ZCode receives an agent-bridge review task containing the source path,
  verification command, and Windows test results.
