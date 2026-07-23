# Task 6 report: safe agent launch and terminal opening

## RED evidence

1. Added the launcher, terminal, and deduplication tests, then ran:

   ```powershell
   $env:PYTHONPATH = (Join-Path $PWD 'src')
   python -m unittest tests.unit.test_launchers tests.unit.test_terminals tests.integration.test_launch_deduplication -v
   ```

   Result: expected `ModuleNotFoundError` errors for
   `agent_bridge.launchers` and `agent_bridge.terminals`.

2. Replaced the obsolete CLI wake-unavailable assertion with a configured
   local-launch contract and ran:

   ```powershell
   python -m unittest tests.integration.test_cli_v2.CliV2Tests.test_wake_uses_the_locally_configured_safe_launcher -v
   ```

   Result: expected `CommandUnavailable: wake is unavailable until launcher
   support is installed`.

3. Added the macOS structured-terminal regression after temporarily removing
   that fallback and ran:

   ```powershell
   python -m unittest tests.unit.test_terminals.TerminalTests.test_macos_terminal_uses_structured_open_arguments -v
   ```

   Result: expected failure because the result was plain instructions rather
   than a `macos-terminal` launch.

## GREEN evidence

Implemented local launch policy, argv-only process creation, a top-level
pickleable dispatcher launch channel, CLI/MCP wake parity, and terminal
fallbacks. The final focused regression run was:

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
python -m unittest tests.unit.test_launchers tests.unit.test_terminals tests.integration.test_launch_deduplication tests.integration.test_dispatcher tests.integration.test_dispatcher_faults tests.integration.test_cli_v2 tests.integration.test_mcp_v2 tests.integration.test_service_workflows -v
python -m compileall -q src tests
git diff --check
```

Result: `Ran 61 tests ... OK`; compilation and whitespace checks passed.

## Delivered behavior

- `evaluate_launch` rejects manual/prompt policies, paths outside a locally
  configured allowlist, malformed or shell-like argv, concurrency overflow,
  and cooldown violations.
- `launch_agent` invokes `subprocess.Popen` with an argv list, explicit
  allowlisted cwd, a minimal environment, `shell=False`, and detached platform
  flags. Task content never supplies argv, cwd, or environment values.
- `LaunchDeliveryChannel` is a top-level pickleable adapter. It reloads target
  profile and project data in the dispatcher worker, applies local policy, and
  reports only `launch_started` evidence after process creation. Dispatcher
  coalescing is covered by a real configured command that records exactly one
  launch for duplicate outbox work.
- `bridge wake AGENT --project PROJECT` uses the same stored policy and reports
  a structured launch result; MCP accepts the matching optional `project`.
- `open_task_terminal` prefers a narrow host protocol
  (`supports_integrated_terminal` plus `open_integrated_terminal(argv,
  workspace)`), then uses `wt.exe`, structured macOS `open --args`, or plain
  current-terminal instructions. Task ID and workspace remain separate argv
  values.

## Self-review and concerns

- The dispatcher enables the built-in channel only when a locally stored auto
  profile has a non-empty launch argv. This keeps manual-only installations
  from starting an extra worker for every ordinary notification, while an auto
  target is always evaluated in the child against its own policy.
- `launch_started` proves process creation only; it intentionally does not
  claim task acknowledgement or completion.
- Python 3.13 was the available launcher in this workspace. The production
  code remains Python 3.9 stdlib compatible.

## Review-fix RED/GREEN (2026-07-24)

### RED

Added tests for concurrent direct wake, a crash after `Popen` but before launch
evidence, manual-recipient routing while an auto launcher is configured, macOS
structured terminal invocation, and shell-injection-safe instructions. Then
ran:

```powershell
$env:PYTHONPATH=(Join-Path $PWD 'src')
python -m unittest tests.unit.test_terminals tests.integration.test_launch_deduplication -v
```

Result: `FAILED (failures=3, errors=2)` as expected. The reservation table and
`_record_started` function did not exist; manual work was retried; macOS used
`open -a Terminal --args`; and plain instructions rendered `task; 123` as raw
shell text.

### GREEN

Added migration `0002_launch_reservations.sql` and schema version 2. The table
uses the task outbox idempotency key (or a deterministic direct-wake key),
stores `reserved`/`started`/`failed`, PID, timestamps, and an expiry. A
`BEGIN IMMEDIATE` reservation checks active reserved/started launches and
cooldown in the same transaction before `Popen`. A `reserved` row is retained
for 300 seconds minimum (or longer for profile cooldown), so a crash after
`Popen` fails closed and retries return that existing reservation without a
second process.

Dispatcher routing now calls the launch channel's narrow applicability check
before a delivery-attempt row is created: manual and prompt profiles are clean
not-applicable work, while invalid auto configuration remains a real retryable
error. The launch adapter remains a top-level pickleable object; the actual
process effect remains inside Task 5's spawned, bounded worker.

macOS now runs a constant `osascript` program with workspace and every command
argument passed separately. The constant program uses AppleScript `quoted
form`; task text is never interpolated into source. Plain instructions use
`shlex.join` on POSIX and `subprocess.list2cmdline` on Windows.

```powershell
$env:PYTHONPATH=(Join-Path $PWD 'src')
python -m unittest tests.unit.test_launchers tests.unit.test_terminals tests.integration.test_launch_deduplication tests.integration.test_dispatcher tests.integration.test_dispatcher_faults tests.integration.test_cli_v2 tests.integration.test_mcp_v2 tests.integration.test_service_workflows -v
python -m compileall -q src tests
git diff --check
```

Result: `Ran 64 tests ... OK`; compilation and whitespace checks passed.

## Re-review P1/P2 RED/GREEN (2026-07-24)

### RED

Updated store expectations for schema v2, added v1-to-v2 and synthetic
v2-to-v3 backup coverage, and added launcher/terminal regressions for malformed
numeric values, embedded-NUL argv, and Windows task IDs containing `&` and `;`.
Then ran:

```powershell
$env:PYTHONPATH=(Join-Path $PWD 'src')
python -m unittest tests.unit.test_store tests.unit.test_launchers tests.unit.test_terminals -v
```

Result: `FAILED (failures=3, errors=1)` as expected. An embedded NUL was
accepted, malformed numeric profile values leaked `ValueError`, and the Windows
fallback emitted copyable-looking raw task text such as `task&calc` and
`task;Start-Process`.

### GREEN

- Store tests now assert version 2, use a synthetic migration 3 with a backup
  baseline at version 2, and create a real v1 database before verifying upgrade
  to the launch-reservation schema.
- `_safe_argv` rejects embedded NULs; `launch_agent` also converts a defensive
  `Popen` `ValueError` into a controlled failed result. Profile numeric limits
  are parsed and range-checked inside the policy-error boundary.
- If Windows Terminal cannot be opened, the fallback deliberately presents a
  JSON argv array labelled **not a shell command**, rather than a string that
  can be pasted into cmd.exe or PowerShell. This keeps `&` and `;` task text as
  data. POSIX still renders a shell-quoted command with `shlex.join`.

```powershell
$env:PYTHONPATH=(Join-Path $PWD 'src')
python -m unittest tests.unit.test_store tests.unit.test_launchers tests.unit.test_terminals tests.integration.test_launch_deduplication tests.integration.test_dispatcher tests.integration.test_dispatcher_faults tests.integration.test_cli_v2 tests.integration.test_mcp_v2 tests.integration.test_service_workflows -v
python -m compileall -q src tests
git diff --check
```

Result: all focused tests passed; compilation and whitespace checks passed.

## Final P1 RED/GREEN (2026-07-24)

### RED

Changed the crash-before-evidence expectation to require a pending result,
then added a dispatcher regression that inserts a durable `reserved` row before
any `Popen` and verifies the subsequent burst. Ran:

```powershell
$env:PYTHONPATH=(Join-Path $PWD 'src')
python -m unittest tests.integration.test_launch_deduplication -v
```

Result: `FAILED (failures=2)` as expected: a live `reserved` row was reported
as `started`, causing the dispatcher to complete the outbox and record
`launch_started` even though no process had been created.

### GREEN

Reservations now retain their persisted state. Reusing `started` returns durable
launch evidence, while reusing `reserved` returns `launch reservation is
pending`. The dispatcher treats that result as retryable work, preserving the
reservation's fail-closed duplicate-process guard without claiming launch
evidence. The pre-`Popen` regression asserts zero completed outbox rows and
zero `launch_started` attempts.

```powershell
$env:PYTHONPATH=(Join-Path $PWD 'src')
python -m unittest tests.integration.test_launch_deduplication tests.integration.test_dispatcher tests.integration.test_dispatcher_faults -v
```

Result: `Ran 24 tests ... OK`.
