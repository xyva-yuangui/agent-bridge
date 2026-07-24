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
# A checkout is a supported development source.  Installing it makes normal
# invocations available; PYTHONPATH remains a safe, non-admin fallback when
# user-site packages are disabled.  Each argument is passed as one array item.
$sourcePackage = Join-Path $sourceRoot "src"
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$sourcePackage;$env:PYTHONPATH" } else { $sourcePackage }
& $pythonPath -m pip install --disable-pip-version-check --no-deps --user $sourceRoot
if ($LASTEXITCODE -ne 0) { Write-Warning "Package installation failed; using this checkout via PYTHONPATH." }

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
