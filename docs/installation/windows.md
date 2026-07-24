# Windows installation

Requirements: Windows 10/11, PowerShell 5.1 or later, and Python 3.9–3.13.
The native notification helper is currently released for Windows x86-64.
Windows on ARM is not a native release target and must be treated as degraded
unless x86-64 emulation is independently verified.
From a source checkout, run:

```powershell
.\install.ps1 -Auto
.\install.ps1 -Agent codex -As codex -Python C:\Python313\python.exe
```

`-Auto` configures detected hosts only. `-Agent` is explicit and may create
managed configuration for that named host. Restart an agent application after
setup. Check the conservative result with:

One `./install.ps1 -Auto` invocation discovers and registers every available
Codex, Claude Code, Reasonix, and ZCode host; it does not stop after the first
match. Hosts that are absent are reported as terminal-fallback/degraded rather
than claimed as installed. `-Agent reasonix` deliberately scopes setup to
Reasonix, even when other hosts are detected.

```powershell
bridge setup status
bridge doctor --strict
bridge status --oneliner
```

The native Windows toast helper is included as a tracked release artifact, but
notification permission, actions, and persistence are real-machine release
checks. A successful helper launch is not an acknowledgement; check delivery
status or the target's `inbox`.

## Repair and removal

```powershell
bridge setup --repair
bridge uninstall
bridge uninstall --purge-data
```

Uninstall removes only Agent Bridge-owned host config, launcher entries, and
receipted helpers. It preserves task data by default. `--purge-data` names the
exact data root before it removes it.
