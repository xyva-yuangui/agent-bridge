# Task 12 reliability report

Date: 2026-07-24 (Windows, Python 3.13)

## Release gates

- Five independent executions of `test_sqlite_concurrency`,
  `test_fault_injection`, `test_performance_budgets`, and
  `test_end_to_end_v2`: all passed.
- Spawn stress used 40 concurrent task creators and 10 concurrent distinct
  claimants. It asserted task/outbox/event counts, unique IDs, contiguous
  per-task revisions, complete event chains, and `PRAGMA integrity_check=ok`.
- The deterministic store/dispatcher fault harness covered
  `before_task_commit`, `after_task_commit`, `after_attempt_recorded`,
  `after_notification_effect`, `after_launch_effect`, and
  `before_outbox_complete`. Each restart retained the durable task and made at
  most one observable effect per idempotency key.
- Launch fault classification is adapter-driven: the production
  `LaunchDeliveryChannel` registered by the CLI identifies itself as a launch
  capability, so aliases such as the CLI's `launcher` do not silently route to
  notification fault handling.
- A hard-exit harness invokes `os._exit(73)` in a child dispatcher after a
  fsynced notification or launch effect. It proves the still-live lease blocks
  an immediate competitor, conditionally waits for lease expiry, then reclaims
  and completes the outbox with exactly one fsynced idempotency key.
- The four-agent workflow covered Codex, Claude, Reasonix, and ZCode with
  one-time ACK proofs, question/answer, changes/reclaim, approval, completion,
  and separate delivery-evidence assertions.

## Performance (1,000 samples each)

| Operation | P95 | Base budget | CI gate (2.0x) |
| --- | ---: | ---: | ---: |
| Task create | 1.013 ms | 50 ms | 100 ms |
| Indexed inbox | 1.291 ms | 100 ms | 200 ms |
| No-work tick | 0.008 ms | 50 ms | 100 ms |
| TUI projection | 2.128 ms | 100 ms | 200 ms |

The documented shared-runner multiplier is exactly 2.0; no looser multiplier
is accepted by the test.

## Platform and suite evidence

- Windows smoke: 15 notification-protocol/TUI tests passed.
- macOS protocol/source/TUI smoke: 21 tests passed. The `smoke_macos.sh`
  wrapper was created but cannot execute on this Windows host because no POSIX
  shell is installed.
- Unit suite: 67 tests passed. `compileall` passed.
- Dispatcher module: all 16 tests passed. The spawn-timeout tests now use the
  worker's readiness signal with a realistic two-second burst, retain their
  timeout/cleanup/effect assertions, and passed ten consecutive repetitions.
