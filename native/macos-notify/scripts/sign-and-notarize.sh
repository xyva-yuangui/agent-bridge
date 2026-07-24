#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
app="$root/dist/AgentBridgeNotifier.app"
identity="${AGENT_BRIDGE_CODESIGN_IDENTITY:-}"
notary_profile="${AGENT_BRIDGE_NOTARY_PROFILE:-}"

if [[ ! -d "$app" ]]; then
  printf 'missing app bundle; run scripts/build-universal2.sh first\n' >&2
  exit 1
fi
if [[ -z "$identity" ]]; then
  printf 'unsigned local development build: AGENT_BRIDGE_CODESIGN_IDENTITY is not set\n'
  exit 0
fi

codesign --force --sign "$identity" --options runtime --timestamp --entitlements "$root/AgentBridgeNotifier.entitlements" "$app"
codesign --verify --deep --strict --verbose=2 "$app"
if [[ -z "$notary_profile" ]]; then
  printf 'signed but not notarized: AGENT_BRIDGE_NOTARY_PROFILE is not set\n'
  exit 0
fi

archive="$root/dist/AgentBridgeNotifier.zip"
ditto -c -k --keepParent "$app" "$archive"
xcrun notarytool submit "$archive" --keychain-profile "$notary_profile" --wait
xcrun stapler staple "$app"
spctl -a -vv "$app"
