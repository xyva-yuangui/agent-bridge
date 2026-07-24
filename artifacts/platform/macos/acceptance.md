# macOS acceptance evidence

Date: 2026-07-24
Candidate: `ce229dbfbae415f270c16db24f690976b294c46b` (Agent Bridge 2.0.0)

## Honest current status

This task ran on Windows. No authenticated macOS Intel or Apple Silicon runner
was available, so none of the following has been executed for this candidate:

- `swift test` or a universal2 Swift build;
- installation of the internal `AgentBridgeNotifier.app` from the final ZIP;
- notification authorization, Notification Center persistence, View/Claim/
  Snooze UI actions, or terminal interaction on macOS;
- `codesign`, `spctl`, notarization, or a real Gatekeeper assessment.

Therefore macOS native notifications are **not** claimed as accepted. On macOS
without the receipted app, Agent Bridge honestly reports terminal fallback/
degraded notification capability. The Swift source, helper protocol tests, and
receipted setup lifecycle are source-level coverage only on this host. The
Windows-run suite did verify that a rejected native-app upgrade restores the
previous app bytes, ownership receipt, and registration, but this is not a
substitute for a macOS execution.

## Required CI/release evidence

The `release.yml` aggregate workflow is responsible for the final primary
cross-platform ZIP. It first obtains the locked Windows helper and a macOS
universal2 `.app`; for signed releases it signs/notarizes/staples the app,
then builds `agent-bridge-<version>-portable.zip`. The `.app` is an internal
notification component inside that ZIP, never a standalone DMG/PKG deliverable.

Before a macOS native capability claim, run on both Intel and Apple Silicon:

```bash
swift test --package-path native/macos-notify
native/macos-notify/scripts/build-universal2.sh
file native/macos-notify/dist/AgentBridgeNotifier.app/Contents/MacOS/AgentBridgeNotifier
lipo -archs native/macos-notify/dist/AgentBridgeNotifier.app/Contents/MacOS/AgentBridgeNotifier
codesign -dvvv --entitlements :- native/macos-notify/dist/AgentBridgeNotifier.app
spctl -a -vv native/macos-notify/dist/AgentBridgeNotifier.app
```

Then extract the assembled ZIP under a path containing spaces and non-ASCII
characters, use `install.sh --auto` with its bundled offline wheel, verify the
receipted app install/repair/uninstall path, and manually record authorization,
Notification Center persistence, all three actions, both integrated and
fallback terminals, migration, and zero idle bridge processes. Only attach
signing/notarization conclusions after those commands and UI checks succeed.
