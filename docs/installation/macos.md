# macOS installation

Requirements: macOS, Bash 3.2 or later, and Python 3.9–3.13. From a source
checkout:

```bash
./install.sh --auto
./install.sh --agent codex --as codex --python /usr/bin/python3
bridge setup status
bridge doctor --strict
```

The installer writes a bounded managed block to `~/.profile` only when needed;
open a new terminal after installation. It configures detected Codex, Claude
Code, Reasonix, and ZCode host surfaces conservatively.

One `./install.sh --auto` invocation discovers and registers every available
host in that set. Missing hosts remain honestly degraded to terminal fallback;
`--agent reasonix` scopes setup to Reasonix rather than modifying every
detected host.

The Swift helper source and universal2 build script ship in the repository.
macOS native notifications, code signing, Gatekeeper acceptance, notarization,
and Intel/Apple Silicon behavior are **not verified by a Windows checkout**.
They require real-machine release evidence on both architectures before a
native capability claim. Until then `bridge setup status` and `bridge doctor`
are authoritative and terminal fallback remains valid.

## Repair and removal

```bash
bridge setup --repair
bridge uninstall
bridge uninstall --purge-data
```

Uninstall preserves task data unless `--purge-data` is explicitly requested.
It removes only configuration, launcher PATH block, and helper files recorded
as owned by Agent Bridge.
