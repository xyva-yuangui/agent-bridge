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
