# Third-party dependencies and licenses

This helper is MIT licensed. Release dependencies are intentionally limited to:

| Crate | License |
| --- | --- |
| serde | MIT OR Apache-2.0 |
| serde_json | MIT OR Apache-2.0 |
| windows | MIT OR Apache-2.0 |
| winreg | MIT |

`cargo metadata --locked --no-deps --format-version 1` is the authoritative
build-time dependency inventory. Keep `target/release/agent-bridge-windows-notify.exe`
at or below 5 MiB; the release verification script records the exact byte count.
