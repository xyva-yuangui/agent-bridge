# Task 8 report: Windows native Toast helper

## TDD evidence

- Added `tests/platform/test_windows_notify_protocol.py` before the Python
  implementation. Its first run failed with
  `ModuleNotFoundError: No module named 'agent_bridge.notifications'`.
- Added the durable dispatcher-mapping test before `WindowsNotificationChannel`.
  It failed with `ImportError: cannot import name 'WindowsNotificationChannel'`.
- Added the degraded-capability test before `windows_notification_capability`.
  It failed with the corresponding import error.
- Added the dispatcher wiring test before the CLI channel registration. It
  failed because dispatch reported zero delivered notifications.

## Implemented

- `WindowsNotifier` sends exactly one bounded UTF-8 JSON request via
  `subprocess.run([...], shell=False)`, with strict decoding, timeout handling,
  output-size rejection, exact response fields, fixed operations, and fixed
  opaque actions (`view`, `claim`, `snooze`). It never passes notification text
  to a shell.
- `WindowsNotificationChannel` writes `notification_mappings` only after a
  helper returns a native ID and `os_posted`, so delivery evidence is not
  fabricated. The dispatcher enables this optional channel only when the
  configured helper is actually a local file.
- `bridge doctor --strict` now exposes `native_notifications` plus detailed
  capability information. A missing helper is explicitly degraded and makes
  strict doctor fail rather than claiming OS delivery.
- Added `native/windows-notify`, a bounded Rust JSON helper protocol with
  `deny_unknown_fields`, strict fixed actions/operations and limits, per-user
  `HKCU\\Software\\Classes` protocol registration, a stable opaque native ID,
  and WinRT `ToastNotificationManager` posting. XML text/attributes are escaped;
  neither PowerShell nor a shell is used. The dependency/license inventory and
  release optimization settings are included in the native directory.

## Verification

- `PYTHONPATH=src py -3 -m unittest tests.platform.test_windows_notify_protocol tests.unit.test_delivery tests.integration.test_dispatcher tests.integration.test_dispatcher_faults tests.integration.test_cli_v2 -v`
  passed: 41 tests.
- `py -3 -m compileall -q src tests integrations` passed.
- `PYTHONPATH=src py -3 -m agent_bridge.cli --json doctor --strict` correctly
  reported `native_notifications: false` and a missing helper. It also reported
  the existing schema-version mismatch: `SCHEMA_VERSION = 2` while migrations
  through `0004` are present; this predates Task 8 and was not changed.

## Platform limitation and evidence

`cargo` and `rustc` were unavailable on `PATH`; both
`C:\\Users\\Administrator\\.cargo\\bin` and conventional `C:\\Program Files\\Rust`
locations were absent. Consequently `cargo test`, `cargo build --release`, real
WinRT registration/post/action smoke testing, Notification Center persistence,
and <=5 MiB binary measurement could not run. No manual Windows evidence is
claimed. The exact evidence record is
`artifacts/platform/windows/task-8-evidence.json`.

## Scope preservation

Task 7 adapters/integrations were left unchanged. The only shared integration
point altered is CLI dispatcher/doctor wiring for the optional Windows helper.
