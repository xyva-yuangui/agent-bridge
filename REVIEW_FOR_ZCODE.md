# ZCode review: Agent Bridge v2

Date: 2026-07-24
Candidate: `e7ea9c8537cc1b463a09642bbf95d5f3abd6c00f`

## Review scope

- Design: `docs/superpowers/specs/2026-07-23-agent-bridge-v2-lightweight-desktop-design.md`
- Plan: `docs/superpowers/plans/2026-07-23-agent-bridge-v2-lightweight-desktop.md`
- Commit range: `06ff3c222f78d20903a95a998de595c023386de5..e7ea9c8537cc1b463a09642bbf95d5f3abd6c00f`
- Delivery model: package-only, SQLite + transactional outbox, short bounded
  dispatch bursts, on-demand terminal TUI, no resident daemon/listener/cloud
  service/default telemetry.
- Primary release model: exactly one cross-platform portable ZIP. The macOS
  `.app` is only an internal notification helper within that ZIP; there is no
  DMG/PKG deliverable and no Windows-only archive may be called final.

## Review requests

Review design conformance, lifecycle authorization/evidence separation,
SQLite concurrency and crash recovery, bounded helper protocols, secret-safe
release signing, managed-config ownership/rollback, package-only portability,
desktop UX/fallback honesty, documentation, and every release artifact.

In particular, verify that one normal `install.ps1 -Auto` can configure every
detected Codex, Claude Code, Reasonix, and ZCode host in one invocation, while
missing hosts degrade rather than being misrepresented as installed. Confirm
that a launch/result never becomes a delivery acknowledgement without an
independent consumer proof.

## Evidence and known limits

- Windows acceptance: `artifacts/platform/windows/acceptance.md`
- macOS acceptance (explicitly pending real machine/CI):
  `artifacts/platform/macos/acceptance.md`
- Capability/degradation matrix: `artifacts/release/capability-matrix.json`
- Acceptance input checksums: `artifacts/release/checksums.txt`

Windows native Toast registration, OS-post result, stored opaque mapping,
action URI forwarding, and unregister were exercised with the verified helper.
Notification Center visual persistence and a physical UI click were not
observed. No macOS machine, Swift toolchain, or final universal2 app was
available locally; do not approve a macOS native-notification release claim
without the CI/real-machine evidence named in the macOS report.

**Current production E2E blocker:** an independent review found that the
dispatcher is not yet wired to deliver HostAdapter session cards; the global
launcher channel can classify a manual recipient as delivered; MCP actor values
can be overridden; setup's production notification activation argv omits
`--as`; the Claude hook/host-consumer workflow is incomplete; and Windows CAS
displaced content can be lost. Treat the attached acceptance documents as
provisional lower-level/package observations. Do not approve this candidate
until those findings are fixed and the four-host production E2E evidence is
rerun.

The local host also lacked `cargo`; the staged locked Windows helper hash was
verified and exercised, while the Rust rebuild remains a CI/release gate.

## Reproduction commands

```powershell
$env:PYTHONPATH = ''
py -3 -m unittest discover -s tests -v
py -3 scripts\bootstrap_wheel.py --check
py -3 -m compileall -q src scripts tests
git diff --check

# Targeted release acceptance:
py -3 -m unittest tests.installers.test_bootstraps tests.test_portable_zip -v
py -3 -m unittest tests.test_cli tests.test_mcp tests.integration.test_migrate_v1 `
  tests.integration.test_sqlite_concurrency tests.integration.test_fault_injection `
  tests.integration.test_end_to_end_v2 tests.integration.test_performance_budgets `
  tests.unit.test_terminals tests.unit.test_tui_model tests.unit.test_tui_render `
  tests.unit.test_tui_controller tests.platform.test_tui_inputs -v
```

On macOS, additionally follow every command and manual acceptance check in
`artifacts/platform/macos/acceptance.md` and the portable ZIP smoke in
`.github/workflows/release.yml` before approving native delivery.

## v1 review-finding disposition

The older v1 acceptance report described file-backed JSON state, portable
locks, wake-based delivery, a PowerShell notification path, and 1.3.0 MCP.
Those findings are superseded rather than carried forward: v2 replaces them
with versioned SQLite migrations/WAL, transactional outbox and durable
delivery attempts, bounded native helper protocols, explicit acknowledgement
evidence, package-only 2.0.0 MCP consumers, and owned/reversible four-host
setup. The v1 review remains historical context only; no v1 behavior should
be used as acceptance evidence for this candidate.

Please return either `approve` or actionable `changes`, identifying severity,
file/line, reproduction, and required evidence. Do not treat macOS source or
CI-only coverage as a real-machine UI acceptance result.
