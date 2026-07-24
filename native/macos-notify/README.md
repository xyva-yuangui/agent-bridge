# Agent Bridge macOS notifier

This is a short-lived Swift/UserNotifications app bundle. It accepts one
bounded JSON request on stdin and writes exactly one JSON response on stdout.
It does not start a shell or receive executable text from notification payloads.
The Python client invokes that mode explicitly as `AgentBridgeNotifier --protocol`;
an app launch with no argument enters the accessory lifecycle, and every other
argument shape is rejected.
Set `AGENT_BRIDGE_MACOS_NOTIFY_ACTIVATION_ARGV` to an installer-owned JSON argv
array whose first item is an absolute local `bridge` executable. Before posting,
the Python channel performs `status -> register -> status` when needed; task
content never enters that argv.

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
macOS supports replacing both pending and delivered local requests by
identifier, but has no API to expire an already-delivered immediate local
notification. For a 1–30 second TTL the helper starts one output-detached,
capped cleanup child; longer TTLs are explicitly reported as unsupported and
are only cleaned on a later helper invocation. The helper is an accessory app
for cold notification activations, exits after an action or 30 seconds, and is
not a resident service.
Each cleanup child carries the persisted expiry generation and removes a stable
notification ID only while that exact generation is still current, so an old
child cannot remove a replacement notification.
