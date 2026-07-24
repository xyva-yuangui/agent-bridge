[CmdletBinding()]
param(
    [switch]$Auto,
    [ValidateSet("codex", "claude", "reasonix", "zcode")]
    [string]$Agent,
    [string]$As,
    [string]$Python,
    [string[]]$WakeArgv,
    [switch]$Uninstall,
    [switch]$PurgeData,
    [switch]$DevSourceFallback,
    [string]$InstallRoot = $env:USERPROFILE
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$sourceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$userRoot = [IO.Path]::GetFullPath($InstallRoot)

function Resolve-Python {
    param([string]$Requested)
    $candidates = @()
    if ($Requested) { $candidates += $Requested }
    $candidates += "python3.exe", "python.exe", "python3", "python"
    foreach ($candidate in $candidates) {
        try {
            $command = Get-Command $candidate -ErrorAction Stop
            $path = (Resolve-Path -LiteralPath $command.Source -ErrorAction Stop).Path
            & $path -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)"
            if ($LASTEXITCODE -eq 0) { return $path }
        } catch { continue }
    }
    throw "Python 3.9 or newer was not found. Pass -Python with an executable path."
}

$pythonPath = Resolve-Python -Requested $Python
& $pythonPath -m pip install --disable-pip-version-check --no-build-isolation --no-deps --user $sourceRoot
if ($LASTEXITCODE -ne 0) {
    if (-not ($DevSourceFallback -or $env:AGENT_BRIDGE_DEV_SOURCE_FALLBACK -eq "1")) {
        throw "Package installation failed. Re-run after fixing pip, or explicitly pass -DevSourceFallback for a degraded checkout-only run."
    }
    Write-Warning "DEGRADED development fallback: importing this checkout through PYTHONPATH."
    $sourcePackage = Join-Path $sourceRoot "src"
    $env:PYTHONPATH = if ($env:PYTHONPATH) { "$sourcePackage;$env:PYTHONPATH" } else { $sourcePackage }
}

$bridgeArgs = @("-m", "agent_bridge.cli")
if ($Uninstall) {
    $bridgeArgs += "uninstall", "--home", $userRoot
    if ($Agent) { $bridgeArgs += "--agent", $Agent }
    if ($PurgeData) { $bridgeArgs += "--purge-data" }
} else {
    $bridgeArgs += "setup", "--home", $userRoot
    if ($Auto) { $bridgeArgs += "--auto" }
    if ($Agent) { $bridgeArgs += "--agent", $Agent }
    if (-not $Auto -and -not $Agent) { $bridgeArgs += "--auto" }
}
if ($As -or $WakeArgv) { Write-Verbose "-As and -WakeArgv are legacy options; setup uses local host scope only." }
& $pythonPath @bridgeArgs
if ($LASTEXITCODE -ne 0) { throw "agent-bridge setup command failed." }
Write-Output "OK agent-bridge lifecycle completed"
