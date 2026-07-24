# Task 11 setup lifecycle report

Date: 2026-07-24 (Windows, non-administrator test process)

Implemented the versioned, reversible setup lifecycle:

- `managed_config` supplies byte-preserving owned blocks, SHA-256 receipts,
  sibling temporary files, `fsync`, replacement, pre-write CAS checking, and
  backups.
- `setup` provides a no-write plan, apply/repair rollback, capability and
  degradation status, conservative ownership-gated uninstall, and explicit
  `.agent-bridge` data purge.
- `bridge setup` and `bridge uninstall` run before service startup, so a
  status query or dry-run does not create a SQLite database.
- The PowerShell and POSIX scripts now resolve Python and invoke the packaged
  CLI using separate arguments. A checkout `PYTHONPATH` fallback supports
  development sources without administrator privileges.

Verification performed:

```text
py -3 -m unittest discover -s tests/installers -v
  19 tests passed (includes a PowerShell runtime install under a temporary
  CJK-and-brackets home directory; 18.276 seconds)

py -3 -m unittest tests.installers tests.unit.test_adapters \
  tests.unit.test_delivery tests.unit.test_store tests.test_cli \
  tests.integration.test_cli_v2 tests.platform.test_windows_notify_protocol -v
  49 tests passed

py -3 -m compileall -q src tests
git diff --check
  both exited successfully
```

The current Windows host had no `bash` executable, so shell runtime execution
was not available; `install.sh` is covered by its static argv-contract test.
