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
$bridgeArgs = @("-m", "agent_bridge.cli")

# Removal is deliberately independent of bootstrap.  Downloading/reinstalling
# a package just to remove it can replace a healthy user installation and can
# never be made safe for a shared interpreter.
if ($Uninstall) {
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    & $pythonPath -c "import agent_bridge.cli" 2>$null
    $packageImportable = $LASTEXITCODE -eq 0
    & $pythonPath -c "import importlib.metadata as m, pathlib, site; d=m.distribution('agent-bridge'); root=pathlib.Path(d.locate_file('')).resolve(); user=pathlib.Path(site.USER_SITE).resolve(); raise SystemExit(0 if root == user or user in root.parents else 1)" 2>$null
    $userOwnedPackage = $LASTEXITCODE -eq 0
    $ErrorActionPreference = $previousPreference
    if (-not $packageImportable) {
        $ownedRuntime = Join-Path $userRoot ".agent-bridge\skill\runtime"
        if (-not (Test-Path -LiteralPath (Join-Path $ownedRuntime "agent_bridge\cli.py"))) {
            throw "Agent Bridge is not importable and its owned runtime is absent; refusing to install anything during uninstall."
        }
        $runtimeProgram = "import sys; sys.path.insert(0, sys.argv[1]); from agent_bridge.cli import main; raise SystemExit(main(sys.argv[2:]))"
        $bridgeArgs = @("-c", $runtimeProgram, $ownedRuntime)
    }
    $bridgeArgs += "uninstall", "--home", $userRoot
    if ($Agent) { $bridgeArgs += "--agent", $Agent }
    if ($PurgeData) { $bridgeArgs += "--purge-data" }
    & $pythonPath @bridgeArgs
    if ($LASTEXITCODE -ne 0) { throw "agent-bridge uninstall command failed." }
    # Only a distribution located in this interpreter's user site can have
    # been installed by this script (--user). pip removes files from that
    # distribution's RECORD only; global, venv, and unrelated packages stay.
    $sharedRuntime = Join-Path $userRoot ".agent-bridge\runtime-receipt.json"
    if ($userOwnedPackage -and -not (Test-Path -LiteralPath $sharedRuntime)) {
        & $pythonPath -m pip uninstall --disable-pip-version-check --yes agent-bridge
        if ($LASTEXITCODE -ne 0) { throw "Agent Bridge lifecycle was removed, but its user-owned Python distribution could not be removed." }
    } elseif (Test-Path -LiteralPath $sharedRuntime) {
        Write-Verbose "Retaining the shared Agent Bridge distribution because other host integrations remain."
    } else {
        Write-Verbose "Retaining Agent Bridge distribution outside this interpreter's user site."
    }
    Write-Output "OK agent-bridge lifecycle completed"
    exit 0
}

$bootstrapWheel = Join-Path $sourceRoot "bootstrap\agent_bridge-2.0.0-py3-none-any.whl"
$bootstrapMetadata = Join-Path $sourceRoot "bootstrap\agent_bridge-2.0.0.bootstrap.json"
if (Test-Path -LiteralPath $bootstrapWheel) {
    if (-not (Test-Path -LiteralPath $bootstrapMetadata)) { throw "Offline bootstrap metadata is missing; use a complete release archive." }
    & $pythonPath -c "import hashlib,json,sys; wheel, metadata=sys.argv[1:]; value=json.load(open(metadata, encoding='utf-8')); actual=hashlib.sha256(open(wheel,'rb').read()).hexdigest(); raise SystemExit(0 if value.get('version') == '2.0.0' and value.get('wheel') == wheel.rsplit('\\',1)[-1] and value.get('sha256') == actual else 1)" $bootstrapWheel $bootstrapMetadata
    if ($LASTEXITCODE -ne 0) { throw "Offline bootstrap metadata does not match its wheel; use a complete verified release archive." }
    & $pythonPath -m pip install --disable-pip-version-check --no-index --no-deps --force-reinstall --user $bootstrapWheel
} else {
    & $pythonPath -c "import setuptools.build_meta"
    if ($LASTEXITCODE -ne 0) {
        throw "Offline bootstrap wheel is missing and setuptools.build_meta is unavailable. Use a complete release archive containing bootstrap/agent_bridge-2.0.0-py3-none-any.whl."
    }
    & $pythonPath -m pip install --disable-pip-version-check --no-build-isolation --no-deps --force-reinstall --user $sourceRoot
}
if ($LASTEXITCODE -ne 0) {
    if (-not ($DevSourceFallback -or $env:AGENT_BRIDGE_DEV_SOURCE_FALLBACK -eq "1")) {
        throw "Package installation failed. Re-run after fixing pip, or explicitly pass -DevSourceFallback for a degraded checkout-only run."
    }
    Write-Warning "DEGRADED development fallback: importing this checkout through PYTHONPATH."
    $sourcePackage = Join-Path $sourceRoot "src"
    $env:PYTHONPATH = if ($env:PYTHONPATH) { "$sourcePackage;$env:PYTHONPATH" } else { $sourcePackage }
}

$bridgeArgs += "setup", "--home", $userRoot
if ($Auto) { $bridgeArgs += "--auto" }
if ($Agent) { $bridgeArgs += "--agent", $Agent }
if (-not $Auto -and -not $Agent) { $bridgeArgs += "--auto" }
if ($As -or $WakeArgv) { Write-Verbose "-As and -WakeArgv are legacy options; setup uses local host scope only." }
& $pythonPath @bridgeArgs
if ($LASTEXITCODE -ne 0) { throw "agent-bridge setup command failed." }
Write-Output "OK agent-bridge lifecycle completed"
