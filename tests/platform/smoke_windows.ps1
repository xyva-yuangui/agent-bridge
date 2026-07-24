$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$env:PYTHONPATH = (Join-Path $root 'src')

# This is intentionally runnable on a Windows host without native helper
# registration.  Protocol tests cover JSON/argv safety and TUI input fallback.
Push-Location $root
try {
    python -m unittest tests.platform.test_windows_notify_protocol tests.platform.test_tui_inputs -v
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}
