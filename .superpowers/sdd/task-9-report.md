# Task 9 report: macOS UserNotifications helper

## Delivered

- Added the `native/macos-notify` SwiftPM macOS app source, strict bounded JSON
  request parser, exact response shape, UserNotifications authorization/status,
  register/unregister/post handling, fixed View/Claim/Snooze category actions,
  opaque ID activation, and safe persisted argv forwarding without a shell.
- Added universal2 assembly, 5 MiB gate, and signing/notarization scripts. The
  scripts read `AGENT_BRIDGE_CODESIGN_IDENTITY` and
  `AGENT_BRIDGE_NOTARY_PROFILE` without printing either value; an absent signing
  identity leaves a clearly reported unsigned local build.
- Added `MacOSNotifier`, `MacOSNotificationChannel`, and platform-specific
  doctor/dispatcher selection. The channel records a notification mapping only
  after the helper reports `os_posted`.
- Added protocol and static macOS bundle tests plus an explicit pending macOS
  real-machine evidence artifact.

## Verification on this host

Passed:

```powershell
$env:PYTHONPATH='src'; py -3 -m unittest tests.platform.test_macos_notify_protocol tests.platform.test_windows_notify_protocol -v
$env:PYTHONPATH='src'; py -3 -m compileall -q src tests
git diff --check
```

Focused result: 15 tests passed. `swift --version` and `bash` are unavailable
on this Windows host, so neither Swift compilation nor shell syntax validation
could run. No authenticated, in-scope remote macOS runner is configured.

The attempted full `unittest discover -s tests -v` reaches the pre-existing
legacy-v1 suites that import `scripts/bridge.py`; they fail because that module
does not expose the old `ensure_dirs` API. The pre-existing documentation test
also expects the obsolete `wake_launched` delivery term. These failures are
outside Task 9 and the v2 focused notifier/integration suites pass.

## Required Task 13 macOS evidence

Run the commands listed in `artifacts/platform/macos/task-9-evidence.json` on
authenticated Intel and Apple Silicon macOS runners, then replace its pending
status with actual command output, signing state, hashes, and action results.
