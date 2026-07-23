# Agent Bridge v2 Lightweight Desktop Collaboration Design

Date: 2026-07-23

Status: Approved in interactive design review

Supersedes: `2026-07-23-agent-bridge-cross-platform-design.md`

## 1. Purpose

Agent Bridge v2 coordinates Codex, Claude Code, Reasonix, and ZCode on one
local machine. It must make task delivery visible and trustworthy, let the
tools exchange tasks and review results automatically according to each
agent's local policy, and remain lightweight enough for an open-source user to
install, move, repair, or remove without operating a background service.

The final product is not a website. Its user-facing surfaces are:

1. a thin integration inside each supported desktop tool;
2. native Windows and macOS notifications;
3. an on-demand terminal TUI for global visibility and recovery.

The browser mockups under `.superpowers/brainstorm/` are design-review
artifacts only. They are not shipped and `.superpowers/` must remain ignored
by Git.

## 2. Success Criteria

The release is successful when:

- a task can be sent, delivered, acknowledged, claimed, completed, questioned,
  answered, reviewed, and returned for changes across all four agent profiles;
- the sender can distinguish “stored”, “OS notification posted”, “shown in the
  target integration”, “target acknowledged”, and “target claimed”;
- Windows 11 and macOS can display native actionable notifications;
- clicking a notification opens the target desktop tool's integrated terminal
  when that capability exists, with Windows Terminal or macOS Terminal as the
  fallback;
- each desktop tool has at least one in-application task entry point, either a
  native panel or a session task card;
- automatic execution follows the target agent's local profile and cannot be
  elevated by the sender;
- idle Agent Bridge has no running bridge process;
- concurrent commands and interrupted dispatch do not lose or duplicate tasks;
- installation, repair, migration, and removal work without administrator
  privileges and without damaging unrelated application configuration;
- a new maintainer can build, test, package, and release the repository from
  documented commands.

## 3. Non-Goals

Version 2 does not provide:

- a continuously running local daemon;
- a browser dashboard or Electron application;
- cloud synchronization or multi-machine database locking;
- remote execution over the network;
- a new programming-agent runtime;
- a requirement that every host expose an identical native sidebar when the
  host has no supported extension API.

The database must stay on a local filesystem. Network shares and consumer sync
folders are unsupported because their locking and durability semantics are not
reliable enough for SQLite WAL.

## 4. Selected Architecture

The selected approach is an event-driven burst dispatcher with thin host
integrations. It is preferred over a permanent service, periodic OS scheduler,
or plugin-only polling.

```text
Codex / Claude / Reasonix / ZCode integration
                 |
                 v
        Stable CLI and MCP protocol
                 |
                 v
  SQLite store + task state machine + outbox
                 |
                 v
     short-lived dispatcher (<= 30 seconds)
          /              |              \
 native notifier    host integration    launch adapter
          \              |              /
                 delivery evidence
                        |
                on-demand terminal TUI
```

Every component has a single responsibility:

- **Store** owns durable state, constraints, migrations, and transactions.
- **State machine** validates task lifecycle transitions.
- **Dispatcher** turns committed outbox records into bounded delivery attempts.
- **Host adapters** detect, install, health-check, and open supported tools.
- **Host integrations** show task context and return delivery acknowledgments.
- **Notification helpers** interact with the operating system only.
- **Launch adapters** start a configured agent safely.
- **TUI** reads state and invokes public application services; it never edits
  the database directly.

## 5. Proposed Repository Layout

```text
roundtable/
  pyproject.toml
  LICENSE
  SECURITY.md
  CONTRIBUTING.md
  README.md
  README.zh-CN.md
  src/agent_bridge/
    __init__.py
    cli.py
    mcp.py
    config.py
    paths.py
    store.py
    models.py
    state_machine.py
    outbox.py
    dispatcher.py
    delivery.py
    launchers.py
    notifications.py
    tui/
    adapters/
      base.py
      codex.py
      claude.py
      reasonix.py
      zcode.py
    migrations/
  native/
    windows-notify/
    macos-notify/
  integrations/
    codex/
    claude/
    reasonix/
    zcode/
  tests/
    unit/
    integration/
    installers/
    platform/
    fixtures/
  docs/
    architecture/
    installation/
    release/
    superpowers/specs/
    superpowers/plans/
```

The current `scripts/bridge.py` monolith is treated as v1 compatibility source,
not as the module structure for v2. Public commands are preserved where
practical, but new code is divided by responsibility.

## 6. Storage and Data Model

The Python standard-library `sqlite3` module is the only required storage
dependency. Each connection enables:

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
```

The database contains at least:

- `schema_migrations`: applied version and checksum;
- `projects`: normalized workspace identity and local path;
- `agents`: identity, capabilities, integration health, heartbeat, and local
  execution policy;
- `tasks`: current task state, sender, assignee, subject, body, priority, and
  monotonic revision;
- `task_events`: immutable lifecycle and audit events;
- `task_dependencies`: task dependency edges;
- `task_artifacts`: file references and review evidence;
- `delivery_attempts`: channel, status, attempt count, timestamps, and error;
- `outbox`: committed work waiting for dispatch;
- `dispatcher_leases`: bounded single-dispatcher leases;
- `notification_mappings`: native notification IDs mapped to task/action;
- `metadata`: installation and protocol metadata.

Task creation and its outbox event occur in one transaction. Every state change
updates the current row and inserts an immutable event in one transaction.
Foreign keys prevent orphan events and deliveries. Unique idempotency keys
prevent duplicate effects after retry.

No v2 mutation is implemented as an unlocked read followed by a later write.
Maintenance, archive, and cleanup use transactions and operate on selected row
IDs inside the same transaction.

## 7. Task and Delivery State Machines

### 7.1 Task lifecycle

The task state is a business fact:

```text
pending -> working
working -> completed
working -> input_required
input_required -> pending
working -> review_requested
review_requested -> completed
review_requested -> changes_requested
changes_requested -> working
working -> failed
```

Only the assignee may claim, ask a question, request review, or complete. Only
the original sender may answer a question or issue a requested review verdict.
The state machine rejects illegal transitions before any database update.

### 7.2 Delivery lifecycle

Delivery is evidence, not task state. Supported evidence values are:

```text
queued
dispatching
os_posted
plugin_delivered
viewed
launch_started
agent_acknowledged
claimed
retry_wait
failed
```

Multiple delivery channels may run in parallel. The user-facing aggregate
status reports the strongest proven evidence while retaining each attempt.

- `os_posted` means the operating system accepted the notification.
- `plugin_delivered` means a host integration accepted the task card.
- `viewed` requires an explicit open/view callback.
- `launch_started` means only that a configured process was started.
- `agent_acknowledged` requires the target integration, `status`, or `inbox`
  to acknowledge actionable work under the target identity.
- `claimed` proves both delivery and ownership.

Neither a zero process exit code nor a successful wake is described as “the
agent received the task”.

## 8. Burst Dispatcher

After a command commits outbox work, it requests a detached
`bridge dispatch --burst`. The command does not wait for all delivery channels,
preserving low send latency.

The dispatcher:

1. acquires a database lease;
2. selects due outbox rows;
3. coalesces duplicate notification and wake intents per task and target;
4. records `dispatching`;
5. invokes notification, integration, and launch adapters;
6. records channel-specific evidence;
7. retries transient failures with bounded exponential backoff and jitter;
8. releases the lease and exits when idle or after 30 seconds.

The lease has an expiry and an owner nonce. A later dispatcher may reclaim an
expired lease without inspecting protected Windows processes. Launch requests
also have idempotency keys, cooldowns, attempt limits, and per-agent concurrency
limits.

There is deliberately no always-running recovery process. Every CLI, MCP,
integration, and TUI entry point performs a cheap nonblocking `tick` that
requests dispatch when due work exists. An optional low-frequency OS recovery
task may be enabled explicitly, but is off by default.

The documented limitation is that if a dispatcher is killed and the user
never invokes any Agent Bridge entry point again, retry waits until the next
entry point. The durable task is not lost.

## 9. Agent Execution Policy

Each target agent owns a local profile:

```json
{
  "execution_policy": "manual",
  "launch_argv": ["codex", "exec"],
  "terminal_preference": "auto",
  "max_concurrency": 1,
  "cooldown_seconds": 30,
  "workspace_allowlist": []
}
```

Policies are:

- `manual`: show notification and task card; the user claims explicitly;
- `prompt`: notification offers an “execute now” action;
- `auto`: launch automatically after local policy checks.

The sender may request automatic handling but cannot raise the target's local
policy. Launching uses an argv array without a shell. Task content cannot
supply executables, flags, working directories, or environment variables.

## 10. Desktop Integrations

The release ships one thin integration package per host. The adapter reports a
capability manifest rather than pretending every host has the same plugin API:

```text
surface: native_panel | session_card | terminal_fallback
can_ack: true|false
can_open_terminal: true|false
can_receive_context: true|false
protocol_version: integer
integration_version: semver
```

Where a supported native extension API exists, the integration provides a
panel or sidebar. Otherwise, the host uses its supported Skill, Hook, MCP, or
prompt integration to show a session task card and acknowledge delivery. A
terminal fallback is retained for degraded operation.

Every host must provide at least one in-application task entry point for a
supported release claim. `bridge setup status` and `bridge doctor --strict`
show the actual capability level:

```text
Codex     native-panel | session-card | terminal-fallback
Claude    native-panel | session-card | terminal-fallback
Reasonix  native-panel | session-card | terminal-fallback
ZCode     native-panel | session-card | terminal-fallback
```

Host integrations do not own storage, locks, task transitions, or retry
policy. They identify the host, report presence and version, render task
context, call the stable CLI/MCP API, and return ACKs.

## 11. Native Notifications and Open Actions

The old Windows `System.Windows.Forms.NotifyIcon` balloon is removed.

### Windows

A small open-source native helper posts a real WinRT Toast under a registered
AUMID. Installation registers the required per-user shortcut or protocol
activation metadata. Toast actions include:

- View task
- Claim or execute, when local policy permits
- Snooze

Activation invokes a constrained Agent Bridge URI/action handler. The handler
maps the native notification ID to a stored task ID and never executes command
text from the notification.

### macOS

A small signed/notarized Swift app helper uses UserNotifications and defines
equivalent actions. It is not a resident menu-bar application. macOS may launch
it to process an action, after which it exits.

### Helper protocol

Helpers receive bounded JSON over stdin and return bounded JSON over stdout.
They have timeouts, stable exit codes, and no shell interpolation. A successful
result includes the native notification identifier. The target is roughly
1–3 MB per helper, with a release ceiling of 5 MB per platform helper.

Notification clicks first request the target host's integrated terminal.
Windows Terminal or macOS Terminal is used when the host adapter cannot open
one. The command is passed as an argument-safe launch request.

A pure-Python distribution may omit helpers. It must report degraded
notification capability clearly and retain integration/TUI delivery.

## 12. Terminal TUI

`bridge tui` is full-screen and starts only on demand. It presents:

- agent presence, capability, health, and execution policy;
- inbox, working, review, completed, and failed counts;
- sortable task list with source, target, subject, state, and delivery evidence;
- task details, dependencies, attempts, review result, and artifacts;
- actions for view, claim, retry, open terminal, filter, and quit.

To avoid a large framework dependency, the first release uses a focused
ANSI/VT renderer with separate Windows (`msvcrt`) and POSIX
(`termios`/`select`) input adapters. Unsupported terminals receive a compact
noninteractive table instead of broken control sequences.

The TUI reads through application services and refreshes every 250–500 ms only
while open. It is not a dispatcher daemon.

## 13. Installation, Repair, Migration, and Removal

The primary distribution is a normal Python package with platform wheels:

- Windows x64;
- Windows ARM64;
- macOS universal2;
- pure-Python degraded wheel/sdist.

Primary installation:

```powershell
py -m pip install --user agent-bridge
bridge setup --auto
```

```bash
python3 -m pip install --user agent-bridge
bridge setup --auto
```

A portable ZIP is provided as a fallback. Homebrew, Scoop, and winget recipes
may follow after the package format is stable. `curl | shell` is not the
primary installation path.

Supported lifecycle commands are:

```text
bridge setup --dry-run
bridge setup --auto
bridge setup --agent <name>
bridge setup --repair
bridge setup status
bridge uninstall
bridge uninstall --purge-data
bridge migrate --from-v1
```

Setup follows this transaction-like process:

1. detect installed hosts and current capability;
2. display the planned changes;
3. back up every file that will change;
4. modify only versioned managed blocks or documented structured keys;
5. install the host integrations and native helper;
6. register local agent profiles and policies;
7. validate syntax and launchability;
8. roll back changed files on failure;
9. run `bridge doctor --strict`;
10. print a capability and degradation report.

Paths are handled as literal paths. Argument arrays are preserved. UTF-8,
existing newline style, non-ASCII names, spaces, and `[]` in paths are covered
by tests.

Uninstall removes only managed integrations, PATH entries, launch metadata,
and installed package files. It preserves the database unless
`--purge-data` is explicit.

The v1 JSON board and configuration are imported once with a backup and an
import ledger. Migration is idempotent. V2 does not dual-write JSON and SQLite.
An export command provides portable JSON for rollback or inspection.

The default data root remains `~/.agent-bridge` for compatibility and
portability. `AGENT_BRIDGE_HOME` may select an explicit local data root.

## 14. Versioning and Compatibility

The project defines separate versions for:

- package;
- database schema;
- CLI/MCP protocol;
- host adapter API;
- host integration;
- native helper protocol.

Compatibility negotiation happens before an integration performs a mutation.
Unsupported major protocol versions fail visibly with repair guidance.

Existing CLI concepts and lifecycle ownership rules remain stable. Tags and
agent capabilities are defined in one canonical schema and generated into
help, skill documentation, and adapters to eliminate drift.

## 15. Reliability, Security, and Degradation

### Reliability

- All writes are transactional.
- Migrations are checksummed and backed up.
- The dispatcher uses expiring database leases, not PID truth checks.
- All external effects use idempotency keys.
- Logs are structured, bounded, rotated, and redact sensitive values.
- Database integrity failure stops writes and offers diagnostic/export/restore
  paths.

### Security

- No network listener is enabled by default.
- No telemetry is enabled by default.
- API keys and environment secrets are not copied into task records or logs.
- Data, configuration, control tokens, and notification activation mappings are
  accessible only to the current OS user.
- Automatic launch uses local allowlists and argv arrays without a shell.
- Notification action payloads contain opaque IDs, not executable commands.
- Native artifacts publish SHA-256 checksums and an SBOM; release builds are
  signed/notarized where the platform supports it.

### Degradation

- Notification failure leaves integration and TUI delivery available.
- Integration failure leaves native notification and terminal fallback
  available.
- Dispatcher death leaves durable queued work for the next tick.
- Missing target software leaves the task queued and never fabricates ACK.
- Database corruption enters read-only recovery mode.
- An unsupported terminal prints a stable table.

## 16. Performance Budgets

Measured on a normal local SSD with a warm Python runtime:

- create task: P95 below 50 ms before asynchronous dispatch;
- inbox query: P95 below 100 ms for the bounded active dataset;
- integration hook: below 150 ms excluding host startup;
- native notification request: visible within 2 seconds;
- TUI refresh interval: 250–500 ms;
- idle bridge processes: zero;
- dispatcher lifetime: at most 30 seconds;
- dispatcher memory target: 15–30 MB;
- platform helper size: target 1–3 MB, ceiling 5 MB.

Queries are indexed by project, assignee, state, updated time, due outbox time,
and idempotency key. TUI and inbox queries are bounded and paginated.

## 17. Test Strategy

### Unit tests

Cover:

- legal and illegal task transitions;
- permissions for assignee and sender operations;
- outbox atomicity and idempotency;
- delivery evidence aggregation;
- lease acquire, expiry, and reclaim;
- retry and cooldown limits;
- launch policy and workspace allowlists;
- adapter capability negotiation;
- configuration managed-block round trips;
- CLI/MCP argument parsing and parity;
- encoding on legacy Windows code pages;
- TUI rendering and key input adapters.

### Integration and fault tests

Cover:

- multi-process create, claim, clean, and migration stress;
- interruption before commit, after commit, during notification, and during
  launch;
- repeated dispatch after each interruption;
- duplicate notification callbacks;
- expired dispatcher recovery;
- v1 migration, repeated migration, export, and restore;
- installation, repeated installation, repair, upgrade, uninstall, and purge;
- paths containing spaces, Chinese characters, and `[]`;
- operation without administrator privileges.

### Platform matrix

CI and release testing cover:

- Windows 11 x64 and ARM64;
- macOS Intel and Apple Silicon;
- Python 3.9 through 3.13;
- platform wheel and pure-Python degraded installation.

Native helper protocol tests run automatically. Real Windows Toast and macOS
UserNotifications display, action, notification-center persistence, and
terminal opening require release smoke tests on real machines.

Each of the four desktop integrations is tested for:

- in-application task visibility;
- delivery ACK;
- view and claim;
- manual, prompt, and auto behavior;
- integrated-terminal opening;
- fallback behavior when the host or integration is unavailable.

## 18. Open-Source Release Requirements

The repository uses Apache-2.0 for its explicit patent grant and permissive
reuse. Before release it contains:

- `LICENSE`;
- `SECURITY.md`;
- `CONTRIBUTING.md`;
- English and Chinese setup, migration, repair, and uninstall documentation;
- architecture and adapter-authoring documentation;
- reproducible helper build instructions;
- CI workflow definitions;
- checksums, SBOM, and signing/notarization documentation;
- a compatibility and degradation matrix.

Release gates:

- all required automated tests pass;
- Windows and macOS real-machine smoke tests pass;
- no open Critical or High defect;
- package install/repair/uninstall round trip passes;
- installed artifact hashes match release artifacts;
- v1 migration evidence is retained;
- ZCode review is approved after requested changes are resolved.

## 19. Disposition of the ZCode v1 Review

The v2 implementation handles the review as follows:

### Eliminated by architecture replacement

The JSON board, `_locked_file`, `_process_exists`, stale lock files,
`atomic_update_board`, `_touch_heartbeat`, and `_maybe_rotate` concurrency
paths are removed rather than patched. SQLite transactions, WAL, and database
leases address the reported shared-lock, protected-process, TOCTOU, initial
creation, heartbeat, and rotation risks.

The NotifyIcon balloon implementation is removed rather than extended.

### Must still be fixed or covered in v2

- literal path handling in the Windows installer;
- safe argv handling and paths with spaces on both platforms;
- deterministic MCP identity parsing;
- macOS notification/action escaping, replaced by the helper protocol;
- notification lifetime and true success semantics;
- managed PATH removal;
- centralized tags and versions;
- coordinator/presence freshness;
- documented cleanup policy;
- encoding-safe activity and CLI output;
- installer, concurrency, notification, migration, and documentation tests.

### Not accepted without reproduction

- The claim that PowerShell `& $exe $arg1 $arg2` inherently breaks ordinary
  spaced arguments is not treated as proven; argv behavior is retained and
  regression-tested.
- The claim that the current `project` command omits directory creation is not
  accepted because it calls the existing directory setup path; v2 still tests
  a fresh data root.
- Reading `inbox` does not perform heavy cleanup. Maintenance runs through
  bounded `tick`/maintenance operations instead.

## 20. ZCode Review Handoff

After implementation and local verification, Codex sends ZCode a formal
Agent Bridge review task. The task body must include:

- this design specification path and commit;
- the implementation plan path and commit;
- the exact implementation commit range;
- a concise architecture summary;
- the v1 review disposition above;
- commands to run unit, integration, installer, and platform tests;
- Windows and macOS smoke-test evidence;
- packaging artifacts, checksums, and capability matrix;
- known degradations and explicit non-goals.

ZCode is asked to review design conformance, code correctness, concurrency,
security, installation safety, portability, UX degradation, tests, and release
documentation. Completion is not reported to the user until ZCode approves or
all requested changes are implemented and re-reviewed.

## 21. Implementation Sequence

The implementation plan must decompose this design into independently
verifiable stages:

1. package skeleton and compatibility contract;
2. SQLite schema, migrations, repositories, and state machine;
3. CLI/MCP parity on the new service layer;
4. outbox and burst dispatcher;
5. safe launch and terminal adapters;
6. native Windows and macOS notification helpers;
7. four host adapters and integrations;
8. on-demand TUI;
9. setup, repair, migration, and uninstall;
10. concurrency, fault, performance, and platform verification;
11. open-source packaging and documentation;
12. ZCode review, remediation, and final acceptance.

Each stage is developed test-first, preserves a runnable main branch, and
includes rollback or compatibility handling where it changes persistent data
or installed configuration.
