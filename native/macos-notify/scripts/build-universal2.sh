#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
name="AgentBridgeNotifier"
app="$root/dist/AgentBridgeNotifier.app"

swift build --package-path "$root" --configuration release --arch x86_64
x86_bin="$(swift build --package-path "$root" --configuration release --arch x86_64 --show-bin-path)/$name"
swift build --package-path "$root" --configuration release --arch arm64
arm_bin="$(swift build --package-path "$root" --configuration release --arch arm64 --show-bin-path)/$name"

rm -rf "$app"
mkdir -p "$app/Contents/MacOS"
lipo -create "$x86_bin" "$arm_bin" -output "$app/Contents/MacOS/$name"
cp "$root/Info.plist" "$app/Contents/Info.plist"

bytes="$(stat -f%z "$app/Contents/MacOS/$name")"
if (( bytes > 5242880 )); then
  printf 'universal2 helper exceeds 5 MiB: %s bytes\n' "$bytes" >&2
  exit 1
fi
file "$app/Contents/MacOS/$name"
printf 'release_bytes=%s\n' "$bytes" > "$root/RELEASE_SIZE.txt"
