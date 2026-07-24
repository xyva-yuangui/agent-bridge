# Windows acceptance evidence

Date: 2026-07-24
Host: Windows (normal, non-administrator user session), Python 3.14.2
Candidate: `e7ea9c8537cc1b463a09642bbf95d5f3abd6c00f` (Agent Bridge 2.0.0)

This report distinguishes an operating-system result from a UI observation.
No claim below means that a Notification Center visual/history entry or a
human click was observed; the host session was automated.

> **Provisional evidence only — production E2E remediation pending.** The
> observations below prove package/bootstrap behavior and the lower-level
> Windows helper lifecycle. An independent review found that the production
> dispatcher/host-consumer chain is not yet an acceptance basis: it does not
> deliver a session card through the HostAdapter, a global launcher channel can
> misclassify a manual recipient, MCP actor identity can be overridden, setup's
> production activation argv lacks `--as`, and the Claude hook/host consumer
> path is incomplete. The Windows CAS displaced-content case also needs repair.
> Do not use this document to claim complete production four-client delivery or
> final Windows release acceptance until those defects are remediated and this
> evidence is rerun.

## One installer invocation and four desktop clients

Command:

```powershell
$env:PYTHONPATH = ''
py -3 -m unittest tests.installers.test_bootstraps tests.test_portable_zip -v
```

Result: `Ran 5 tests in 23.779s`, `OK`.

The all-host acceptance creates an isolated CJK-and-space user home and four
ordinary host configurations (Codex, Claude Code, Reasonix, and ZCode). One
`install.ps1 -Auto` invocation installs the bundled offline wheel under an
isolated `PYTHONUSERBASE` with no `PYTHONPATH`, registers all four managed
integrations, verifies that `agent_bridge` imports from `site-packages` rather
than `src/`, starts every exact receipt MCP entrypoint from outside the
checkout, repairs the managed blocks, and uninstalls them while preserving
unrelated TOML/JSON content. A separate normal-user bootstrap test covers the
single-host path. No administrator elevation is used.

The portable ZIP test is a real Windows extraction/install test under a
CJK-and-space path. It assembles a test archive with a fixture macOS app,
extracts it, runs `install.ps1` from the extracted files with no checkout or
`PYTHONPATH`, and verifies the bundled wheel/native helper installation. It
does **not** represent the final release ZIP: this Windows host cannot build a
signed universal2 macOS app. The only final primary artifact is the dual-platform
`agent-bridge-<version>-portable.zip` made by the aggregate release workflow.

## Native Windows Toast protocol and action routing

Published helper:

```text
src/agent_bridge/native/windows-x86_64/agent-bridge-windows-notify.exe
SHA-256 C0480D0F10DDF549E8F70B11094DBC29690E63202DE24F33B326E1C20661B22A
size     536,576 bytes (< 5 MiB)
```

The same SHA-256 is recorded in
`native/windows-notify/dist/windows-x86_64/build.json` and was measured from
the staged source package helper during this acceptance.

Real per-user protocol lifecycle (with a temporary data root):

```powershell
# helper receives bounded JSON on stdin
{"operation":"register","activation_argv":["<absolute py.exe>","-3","-m","agent_bridge.cli","--data-root","<temporary data root>","--as","receiver"]}
{"operation":"status"}
bridge --data-root <temporary data root> --as sender send --to receiver ...
bridge --data-root <temporary data root> --as sender dispatch --burst
agent-bridge-windows-notify.exe action-uri agent-bridge://action/view/<opaque-notification-id>
{"operation":"unregister"}
{"operation":"status"}
```

Observed results:

- registration returned `ok=true`, then `status` confirmed the per-user
  `agent-bridge` protocol and AUMID Start Menu shortcut;
- the native dispatcher returned `delivered=1`, persisted an opaque mapping
  such as `toast-af187cce7deb6730`, and the helper returned `os_posted` for the
  Windows toast;
- direct `open-action` and the helper's constrained `action-uri` forwarding
  both resolved that stored mapping to the expected task (the supplied task ID
  is not trusted);
- unregister returned success and the final status correctly reported the
  protocol missing.

The helper's successful `os_posted` response is OS acceptance of the toast.
Notification Center persistence/history and a physical View/Claim/Snooze click
were not visually inspected in this automated session and remain manual
release checks; they are not asserted as completed here.

`cargo` was unavailable on this host, so `cargo test` and a local locked Rust
rebuild were not run. The checked-in release binary/hash was used for the
runtime test; the locked Rust build remains a required Windows CI/release gate.

## TUI, encoding, terminal, migration, reliability, and idle state

Commands:

```powershell
py -3 -m agent_bridge.cli --data-root C:\Temp\AgentBridgeAcceptance20260724\data --as receiver tui --project default
py -3 -m unittest tests.test_cli tests.test_mcp tests.integration.test_migrate_v1 `
  tests.integration.test_sqlite_concurrency tests.integration.test_fault_injection `
  tests.integration.test_end_to_end_v2 tests.integration.test_performance_budgets `
  tests.unit.test_terminals tests.unit.test_tui_model tests.unit.test_tui_render `
  tests.unit.test_tui_controller tests.platform.test_tui_inputs -v
```

Results:

- the redirected desktop-terminal invocation returned `0` and rendered the
  compact task table (no resident dashboard process);
- 38 tests passed in 17.324 seconds, including GBK-safe CLI/MCP transport,
  v1 import/export and concurrent import, the 40-sender/10-claimer SQLite
  stress case, six fault points, four-agent lifecycle evidence, Windows
  terminal fallback with metacharacter safety, and TUI console restoration;
- measured P95s: create `1.015 ms`, inbox `1.620 ms`, idle tick `0.008 ms`,
  TUI projection `2.130 ms` (documented CI multiplier `2.0`);
- after the run, the process query filtered for `agent|bridge|notify` found no
  Agent Bridge process. The only matching unrelated process was
  `lghub_agent.exe` (PID 11196). This is consistent with the bounded,
  on-demand dispatcher/TUI design.

The comprehensive Python suite, bootstrap-wheel validation, `compileall`, and
`git diff --check` are also required final gates and are listed in
`REVIEW_FOR_ZCODE.md` for an independent rerun. They do not supersede the
production E2E remediation noted above.
