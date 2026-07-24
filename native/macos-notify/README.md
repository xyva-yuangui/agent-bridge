# Agent Bridge macOS notifier

This is a short-lived Swift/UserNotifications app bundle. It accepts one
bounded JSON request on stdin and writes exactly one JSON response on stdout.
It does not start a shell or receive executable text from notification payloads.

Build a universal2 development bundle on macOS:

```bash
native/macos-notify/scripts/build-universal2.sh
native/macos-notify/scripts/sign-and-notarize.sh
```

The build script assembles `dist/AgentBridgeNotifier.app`, checks the universal
binary is no more than 5 MiB, and prints `file` output. The signing script is
safe for local development: without `AGENT_BRIDGE_CODESIGN_IDENTITY` it leaves
the bundle unsigned and states that clearly. For distribution, set the signing
identity and optional `AGENT_BRIDGE_NOTARY_PROFILE` in the environment; neither
value is printed or persisted by the scripts.

Run the real-machine smoke sequence for Task 13 on both Apple Silicon and Intel:

```bash
swift test --package-path native/macos-notify
native/macos-notify/scripts/build-universal2.sh
codesign -dvvv --entitlements :- native/macos-notify/dist/AgentBridgeNotifier.app
spctl -a -vv native/macos-notify/dist/AgentBridgeNotifier.app
```

Then register a fixed absolute `bridge` argv, post a task, inspect Notification
Center, exercise View/Claim/Snooze, and record authorization, persistence,
terminal fallback, architecture, signing, and hashes in the evidence artifact.
macOS supports replacing a pending local request by identifier; it has no API
to expire an already-delivered immediate local notification, so expiry is
validated but reported as unsupported rather than claimed.
