# Task 12 reliability report

Date: 2026-07-24 (Windows, Python 3.13)

## Release gates

- Five independent executions of `test_sqlite_concurrency`,
  `test_fault_injection`, and `test_end_to_end_v2`: all passed.
- Spawn stress used 40 concurrent task creators and 10 concurrent distinct
  claimants. It asserted task/outbox/event counts, unique IDs, contiguous
  per-task revisions, complete event chains, and `PRAGMA integrity_check=ok`.
- The deterministic store/dispatcher fault harness covered
  `before_task_commit`, `after_task_commit`, `after_attempt_recorded`,
  `after_notification_effect`, `after_launch_effect`, and
  `before_outbox_complete`. Each restart retained the durable task and made at
  most one observable effect per idempotency key.
- The four-agent workflow covered Codex, Claude, Reasonix, and ZCode with
  one-time ACK proofs, question/answer, changes/reclaim, approval, completion,
  and separate delivery-evidence assertions.

## Performance (1,000 samples each)

| Operation | P95 | Base budget | CI gate (2.0x) |
| --- | ---: | ---: | ---: |
| Task create | 0.983 ms | 50 ms | 100 ms |
| Indexed inbox | 1.366 ms | 100 ms | 200 ms |
| No-work tick | 0.014 ms | 50 ms | 100 ms |
| TUI projection | 2.364 ms | 100 ms | 200 ms |

The documented shared-runner multiplier is exactly 2.0; no looser multiplier
is accepted by the test.

## Platform and suite evidence

- Windows smoke: 15 notification-protocol/TUI tests passed.
- macOS protocol/source/TUI smoke: 21 tests passed. The `smoke_macos.sh`
  wrapper was created but cannot execute on this Windows host because no POSIX
  shell is installed.
- Unit suite: 67 tests passed. `compileall` passed.
- The broad integration suite retains two pre-existing Windows timing failures:
  `test_normal_timeout_uses_conclusive_cleanup_without_live_child` and
  `test_repeated_timeouts_leave_no_delivery_workers`. Both demand a spawned
  child enter its adapter within 0.5 seconds while the dispatcher must still
  reserve cleanup time inside a 0.8-second burst. The Task 12 stress/fault
  gates use synchronization and pass; no timeout or assertion was weakened.
