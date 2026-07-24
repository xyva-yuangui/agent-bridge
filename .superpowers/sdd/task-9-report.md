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

## Review fixes

- The executable now has two bounded modes: stdin protocol mode for Python and
  an `LSUIElement` accessory lifecycle for cold macOS notification actions. It
  retains the UserNotifications delegate, forwards only an opaque activation
  URI through persisted fixed argv, and exits after the action or 30 seconds.
- The notification channel requires a fixed installer-owned activation argv and
  performs `status -> register -> status` before a first post. It cannot place
  task title/body content in that argv.
- Replacement removes pending *and* delivered requests. macOS has no native
  expiration for an already-delivered immediate request: a detached cleanup
  child is capped at 30 seconds, and longer TTLs explicitly report unsupported
  expiration and are cleaned on a later invocation.
- macOS capability/doctor output now separates signature state (`unsigned`,
  `ad_hoc`, `signed`, `notarized`, or `unknown`) from Gatekeeper assessment.
  Strict doctor rejects unsigned/ad-hoc helpers; non-strict local development
  remains usable.

## Verification on this host

Passed:

```powershell
$env:PYTHONPATH='src'; py -3 -m unittest tests.platform.test_macos_notify_protocol tests.platform.test_windows_notify_protocol -v
$env:PYTHONPATH='src'; py -3 -m compileall -q src tests
git diff --check
```

Focused result: 32 notifier and v2 CLI regression tests passed. `swift --version` and `bash` are unavailable
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
