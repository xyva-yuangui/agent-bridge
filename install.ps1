[CmdletBinding()]
param(
    [switch]$Auto,
    [ValidateSet("codex", "claude", "reasonix", "zcode")]
    [string]$Agent,
    [string]$As,
    [string]$Python,
    [string[]]$WakeArgv,
    [switch]$Uninstall,
    [string]$InstallRoot = $env:USERPROFILE
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$script:SourceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:UserRoot = [IO.Path]::GetFullPath($InstallRoot)
$script:BridgeHome = Join-Path $script:UserRoot ".agent-bridge"
$script:SkillHome = Join-Path $script:BridgeHome "skill"
$script:LauncherHome = Join-Path $script:UserRoot ".local\bin"
$script:NotifierHome = Join-Path $script:BridgeHome "native"

function Resolve-Python {
    param([string]$Requested)
    $candidates = New-Object System.Collections.Generic.List[string]
    if ($Requested) {
        $candidates.Add($Requested)
    }
    foreach ($name in @("python3.exe", "python.exe", "python3", "python")) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command -and $command.Source) {
            $candidates.Add($command.Source)
        }
    }
    foreach ($candidate in $candidates) {
        try {
            $resolved = (Resolve-Path -LiteralPath $candidate -ErrorAction Stop).Path
            & $resolved -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)"
            if ($LASTEXITCODE -eq 0) {
                return $resolved
            }
        } catch {
            continue
        }
    }
    throw "Python 3.9 or newer was not found. Pass -Python with an absolute executable path."
}

function ConvertTo-TomlLiteral {
    param([string]$Value)
    return "'" + $Value.Replace("'", "''") + "'"
}

function Set-ManagedBlock {
    param(
        [string]$Path,
        [string]$Name,
        [string]$Body
    )
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $start = "# >>> agent-bridge:$Name >>>"
    $end = "# <<< agent-bridge:$Name <<<"
    $content = ""
    if (Test-Path -LiteralPath $Path) {
        $content = Get-Content -Raw -Encoding UTF8 -LiteralPath $Path
        if ($null -eq $content) {
            $content = ""
        }
    }
    $pattern = "(?ms)^" + [regex]::Escape($start) + ".*?^" + [regex]::Escape($end) + "\s*"
    $content = [regex]::Replace($content, $pattern, "")
    $block = "$start`r`n$Body`r`n$end`r`n"
    $content = $content.TrimEnd() + "`r`n`r`n" + $block
    [IO.File]::WriteAllText($Path, $content.TrimStart(), (New-Object Text.UTF8Encoding($false)))
}

function Remove-ManagedBlock {
    param(
        [string]$Path,
        [string]$Name
    )
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    $start = "# >>> agent-bridge:$Name >>>"
    $end = "# <<< agent-bridge:$Name <<<"
    $content = Get-Content -Raw -Encoding UTF8 -LiteralPath $Path
    if ($null -eq $content) {
        $content = ""
    }
    $pattern = "(?ms)^" + [regex]::Escape($start) + ".*?^" + [regex]::Escape($end) + "\s*"
    $content = [regex]::Replace($content, $pattern, "")
    [IO.File]::WriteAllText($Path, $content.TrimEnd() + "`n", (New-Object Text.UTF8Encoding($false)))
}

function Install-Shared {
    param([string]$PythonPath)
    New-Item -ItemType Directory -Force -Path $script:BridgeHome | Out-Null
    $stage = Join-Path $script:BridgeHome (".skill-stage-" + $PID)
    $backup = Join-Path $script:BridgeHome (".skill-backup-" + $PID)
    foreach ($candidate in @($stage, $backup)) {
        if (Test-Path -LiteralPath $candidate) {
            Remove-Item -LiteralPath $candidate -Recurse -Force
        }
    }
    New-Item -ItemType Directory -Force -Path (Join-Path $stage "scripts") | Out-Null
    # The complete directory includes bridge.py, bridge_mcp.py, and notify_windows.ps1.
    Copy-Item -Path (Join-Path $script:SourceRoot "scripts\*") -Destination (Join-Path $stage "scripts") -Recurse -Force
    New-Item -ItemType Directory -Force -Path (Join-Path $stage "runtime") | Out-Null
    Copy-Item -LiteralPath (Join-Path $script:SourceRoot "src\agent_bridge") -Destination (Join-Path $stage "runtime\agent_bridge") -Recurse -Force
    foreach ($name in @("SKILL.md", "README.md", "README.zh-CN.md")) {
        $source = Join-Path $script:SourceRoot $name
        if (Test-Path -LiteralPath $source) {
            Copy-Item -LiteralPath $source -Destination (Join-Path $stage $name) -Force
        }
    }
    try {
        if (Test-Path -LiteralPath $script:SkillHome) {
            Move-Item -LiteralPath $script:SkillHome -Destination $backup
        }
        Move-Item -LiteralPath $stage -Destination $script:SkillHome
        if (Test-Path -LiteralPath $backup) {
            Remove-Item -LiteralPath $backup -Recurse -Force
        }
    } catch {
        if ((-not (Test-Path -LiteralPath $script:SkillHome)) -and (Test-Path -LiteralPath $backup)) {
            Move-Item -LiteralPath $backup -Destination $script:SkillHome
        }
        throw
    }

    New-Item -ItemType Directory -Force -Path $script:LauncherHome | Out-Null
    $bridgeScript = Join-Path $script:SkillHome "scripts\bridge.py"
    $launcher = Join-Path $script:LauncherHome "bridge.cmd"
    $launcherText = "@echo off`r`n`"$PythonPath`" `"$bridgeScript`" %*`r`n"
    [IO.File]::WriteAllText($launcher, $launcherText, [Text.Encoding]::ASCII)

    $skillLinkParent = Join-Path $script:UserRoot ".agents\skills"
    $skillLink = Join-Path $skillLinkParent "agent-bridge"
    New-Item -ItemType Directory -Force -Path $skillLinkParent | Out-Null
    if (Test-Path -LiteralPath $skillLink) {
        Remove-Item -LiteralPath $skillLink -Recurse -Force
    }
    try {
        New-Item -ItemType Junction -Path $skillLink -Target $script:SkillHome | Out-Null
    } catch {
        Copy-Item -LiteralPath $script:SkillHome -Destination $skillLink -Recurse -Force
    }

    if ($script:UserRoot.TrimEnd("\") -ieq $env:USERPROFILE.TrimEnd("\")) {
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        $parts = @($userPath -split ";" | Where-Object { $_ })
        if (-not ($parts | Where-Object { $_.TrimEnd("\") -ieq $script:LauncherHome.TrimEnd("\") })) {
            $newPath = (($parts + $script:LauncherHome) -join ";")
            [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        }
    }
}

function Read-WindowsNotifierReceipt {
    param(
        [string]$ReceiptPath,
        [string]$ExpectedHelper
    )
    try {
        $receipt = Get-Content -Raw -Encoding UTF8 -LiteralPath $ReceiptPath | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "Invalid Windows notifier ownership receipt."
    }
    $actualNames = @($receipt.PSObject.Properties.Name | Sort-Object)
    $expectedNames = @("helper_path", "owner", "schema", "sha256") | Sort-Object
    if (($actualNames -join "`n") -ne ($expectedNames -join "`n")) {
        throw "Invalid Windows notifier ownership receipt schema."
    }
    if ([int]$receipt.schema -ne 1 -or [string]$receipt.owner -ne "agent-bridge.windows-notify") {
        throw "Invalid Windows notifier ownership receipt owner."
    }
    $expected = [IO.Path]::GetFullPath($ExpectedHelper)
    try {
        $recorded = [IO.Path]::GetFullPath([string]$receipt.helper_path)
    } catch {
        throw "Invalid Windows notifier ownership receipt path."
    }
    if ($recorded -ine $expected) {
        throw "Invalid Windows notifier ownership receipt path."
    }
    if (
        [string]$receipt.sha256 -notmatch '^[0-9A-Fa-f]{64}$' -or
        -not (Test-Path -LiteralPath $expected -PathType Leaf) -or
        (Get-FileHash -Algorithm SHA256 -LiteralPath $expected).Hash -ine [string]$receipt.sha256
    ) {
        throw "Windows notifier ownership hash mismatch."
    }
    return $receipt
}

function Invoke-WindowsNotifierRegistration {
    param(
        [string]$Helper,
        [string]$Request,
        [string]$Operation
    )
    $raw = $Request | & $Helper
    $exitCode = $LASTEXITCODE
    try {
        $result = $raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "Windows notifier $Operation returned malformed JSON."
    }
    if (
        $exitCode -ne 0 -or
        -not $result.ok -or
        $result.notification_id -ne "registration" -or
        $result.status -ne "os_posted"
    ) {
        throw "Windows notifier $Operation returned an invalid result."
    }
    return $result
}

function Install-WindowsNotifier {
    param(
        [string]$PythonPath,
        [switch]$Preflight
    )
    $source = Join-Path $script:SourceRoot "native\windows-notify\dist\windows-x86_64\agent-bridge-windows-notify.exe"
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        $source = Join-Path $script:SourceRoot "native\windows-notify\target\x86_64-pc-windows-gnu\release\agent-bridge-windows-notify.exe"
    }
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Windows notifier release helper is missing; use a source distribution containing native/windows-notify/dist."
    }
    $destination = [IO.Path]::GetFullPath((Join-Path $script:NotifierHome "agent-bridge-windows-notify.exe"))
    $receiptPath = Join-Path $script:NotifierHome "receipt.json"
    $priorProcess = $env:AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER
    $priorUser = [Environment]::GetEnvironmentVariable("AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER", "User")
    $hasHelper = Test-Path -LiteralPath $destination -PathType Leaf
    $hasReceipt = Test-Path -LiteralPath $receiptPath -PathType Leaf
    if ($hasHelper -ne $hasReceipt) {
        throw "Refusing to overwrite an unowned or incomplete Windows notifier installation."
    }
    $repair = $hasHelper -and $hasReceipt
    $originalReceipt = $null
    if ($repair) {
        Read-WindowsNotifierReceipt -ReceiptPath $receiptPath -ExpectedHelper $destination | Out-Null
        $originalReceipt = [IO.File]::ReadAllBytes($receiptPath)
    }
    if (
        ($priorProcess -and [IO.Path]::GetFullPath($priorProcess) -ine $destination) -or
        ($priorUser -and [IO.Path]::GetFullPath($priorUser) -ine $destination)
    ) {
        throw "Refusing to overwrite an unrelated Windows notifier environment value."
    }
    if ($Preflight) { return }

    New-Item -ItemType Directory -Force -Path $script:NotifierHome | Out-Null
    $staged = Join-Path $script:NotifierHome ("notifier-stage-" + $PID + ".exe")
    $backup = Join-Path $script:NotifierHome ("notifier-backup-" + $PID + ".exe")
    $activationArgv = @(
        [IO.Path]::GetFullPath($PythonPath),
        [IO.Path]::GetFullPath((Join-Path $script:SkillHome "scripts\bridge.py")),
        "--data-root",
        [IO.Path]::GetFullPath($script:BridgeHome)
    )
    $request = '{"operation":"register","activation_argv":' + ($activationArgv | ConvertTo-Json -Compress) + '}'
    try {
        Copy-Item -LiteralPath $source -Destination $staged -Force
        if ($repair) {
            Move-Item -LiteralPath $destination -Destination $backup
        }
        Move-Item -LiteralPath $staged -Destination $destination
        $env:AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER = $destination
        [Environment]::SetEnvironmentVariable("AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER", $destination, "User")
        Invoke-WindowsNotifierRegistration -Helper $destination -Request $request -Operation "registration" | Out-Null
        $newReceipt = [ordered]@{
            schema = 1
            owner = "agent-bridge.windows-notify"
            helper_path = $destination
            sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $destination).Hash
        }
        $newReceipt | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath $receiptPath
        if (Test-Path -LiteralPath $backup) {
            Remove-Item -LiteralPath $backup -Force
        }
    } catch {
        $installError = $_
        if (Test-Path -LiteralPath $destination -PathType Leaf) {
            try {
                Invoke-WindowsNotifierRegistration -Helper $destination -Request '{"operation":"unregister"}' -Operation "unregister" | Out-Null
            } catch {}
            Remove-Item -LiteralPath $destination -Force -ErrorAction SilentlyContinue
        }
        if ($repair -and (Test-Path -LiteralPath $backup -PathType Leaf)) {
            Move-Item -LiteralPath $backup -Destination $destination
            [IO.File]::WriteAllBytes($receiptPath, $originalReceipt)
            try {
                Invoke-WindowsNotifierRegistration -Helper $destination -Request $request -Operation "registration rollback" | Out-Null
            } catch {}
        } else {
            Remove-Item -LiteralPath $receiptPath -Force -ErrorAction SilentlyContinue
        }
        $env:AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER = $priorProcess
        [Environment]::SetEnvironmentVariable("AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER", $priorUser, "User")
        Remove-Item -LiteralPath $staged -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
        if ((Test-Path -LiteralPath $script:NotifierHome) -and -not (Get-ChildItem -LiteralPath $script:NotifierHome -Force)) {
            Remove-Item -LiteralPath $script:NotifierHome -Force
        }
        throw $installError
    }
}

function Uninstall-WindowsNotifier {
    $receiptPath = Join-Path $script:NotifierHome "receipt.json"
    $expected = [IO.Path]::GetFullPath((Join-Path $script:NotifierHome "agent-bridge-windows-notify.exe"))
    $hasHelper = Test-Path -LiteralPath $expected -PathType Leaf
    $hasReceipt = Test-Path -LiteralPath $receiptPath -PathType Leaf
    if (-not $hasHelper -and -not $hasReceipt) { return }
    if ($hasHelper -ne $hasReceipt) {
        throw "Refusing to remove an unowned or incomplete Windows notifier installation."
    }
    Read-WindowsNotifierReceipt -ReceiptPath $receiptPath -ExpectedHelper $expected | Out-Null
    Invoke-WindowsNotifierRegistration -Helper $expected -Request '{"operation":"unregister"}' -Operation "unregister" | Out-Null
    if ($env:AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER -ieq $expected) {
        Remove-Item Env:AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER
    }
    if ([Environment]::GetEnvironmentVariable("AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER", "User") -ieq $expected) {
        [Environment]::SetEnvironmentVariable("AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER", $null, "User")
    }
    Remove-Item -LiteralPath $expected -Force
    Remove-Item -LiteralPath $receiptPath -Force
    if (-not (Get-ChildItem -LiteralPath $script:NotifierHome -Force)) {
        Remove-Item -LiteralPath $script:NotifierHome -Force
    }
}

function Get-ExistingWakeArgv {
    param([string]$Name)
    $profilePath = Join-Path $script:BridgeHome "agents\$Name\agent.json"
    if (-not (Test-Path -LiteralPath $profilePath)) {
        return @()
    }
    try {
        $profile = Get-Content -Raw -Encoding UTF8 -LiteralPath $profilePath | ConvertFrom-Json
        if ($profile.wake_argv) {
            return @($profile.wake_argv | ForEach-Object { [string]$_ })
        }
        if ($profile.wake -and ([string]$profile.wake -match '^(.+?\.exe)(?:\s+(.*))?$')) {
            $result = @($Matches[1])
            if ($Matches[2]) {
                $result += @($Matches[2] -split '\s+' | Where-Object { $_ })
            }
            return $result
        }
    } catch {
        return @()
    }
    return @()
}

function Find-WakeArgv {
    param([string]$Name)
    $names = switch ($Name) {
        "codex" { @("codex.exe", "codex") }
        "claude" { @("claude.exe", "claude") }
        "reasonix" { @("reasonix-cli.exe", "reasonix-cli", "reasonix") }
        "zcode" { @("zcode.exe", "zcode") }
    }
    foreach ($commandName in $names) {
        $command = Get-Command $commandName -ErrorAction SilentlyContinue
        if ($command -and $command.Source) {
            $args = @($command.Source)
            if ($Name -eq "codex") { $args += "exec" }
            if ($Name -eq "reasonix") { $args += "run" }
            return $args
        }
    }
    return @(Get-ExistingWakeArgv -Name $Name)
}

function Register-AgentProfile {
    param(
        [string]$Name,
        [string[]]$Wake
    )
    $skills = switch ($Name) {
        "codex" { @("architecture", "hard-reasoning", "complex-impl", "orchestrate") }
        "claude" { @("frontend", "ui", "writing", "analysis") }
        "reasonix" { @("review", "plan", "headless", "refactor") }
        "zcode" { @("review", "testing", "implementation", "acceptance") }
    }
    $strengths = ($skills -join ", ")
    $profile = [ordered]@{
        name = $Name
        skills = $skills
        strengths = $strengths
        last_seen = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
    }
    if ($Wake -and $Wake.Count -gt 0) {
        $profile["wake_argv"] = @($Wake)
    }
    $directory = Join-Path $script:BridgeHome "agents\$Name"
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
    $json = $profile | ConvertTo-Json -Depth 8
    [IO.File]::WriteAllText((Join-Path $directory "agent.json"), $json + "`n", (New-Object Text.UTF8Encoding($false)))
}

function Configure-Codex {
    param([string]$PythonPath)
    $config = Join-Path $script:UserRoot ".codex\config.toml"
    $mcp = Join-Path $script:SkillHome "scripts\bridge_mcp.py"
    $body = @"
[mcp_servers.agent_bridge]
command = $(ConvertTo-TomlLiteral $PythonPath)
args = [$(ConvertTo-TomlLiteral $mcp), '--as', 'codex']
startup_timeout_sec = 15
tool_timeout_sec = 60
"@
    Set-ManagedBlock -Path $config -Name "codex-mcp" -Body $body
    $directive = @"
At the start of every turn, call agent-bridge status and inbox. Handle pending bridge tasks before replying. Use agent-bridge to coordinate work with registered agents.
"@
    Set-ManagedBlock -Path (Join-Path $script:UserRoot "AGENTS.md") -Name "directive" -Body $directive
}

function Set-JsonPromptHook {
    param(
        [string]$Path,
        [string]$Identity,
        [string]$PythonPath
    )
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $root = New-Object PSObject
    if (Test-Path -LiteralPath $Path) {
        try {
            $root = Get-Content -Raw -Encoding UTF8 -LiteralPath $Path | ConvertFrom-Json
        } catch {
            throw "Cannot update invalid JSON config: $Path"
        }
    }
    if (-not $root.PSObject.Properties["hooks"]) {
        $root | Add-Member -NotePropertyName hooks -NotePropertyValue (New-Object PSObject)
    }
    $hooks = $root.hooks
    $current = @()
    if ($hooks.PSObject.Properties["UserPromptSubmit"]) {
        $current = @($hooks.UserPromptSubmit | Where-Object {
            (($_ | ConvertTo-Json -Depth 12 -Compress) -notmatch '\.agent-bridge.*bridge\.py')
        })
    }
    $bridgeScript = Join-Path $script:SkillHome "scripts\bridge.py"
    $command = "`"$PythonPath`" `"$bridgeScript`" --as $Identity status --oneliner"
    $entry = [ordered]@{
        matcher = ""
        hooks = @([ordered]@{ type = "command"; command = $command; timeout = 10 })
    }
    $updated = @($current + $entry)
    if ($hooks.PSObject.Properties["UserPromptSubmit"]) {
        $hooks.UserPromptSubmit = $updated
    } else {
        $hooks | Add-Member -NotePropertyName UserPromptSubmit -NotePropertyValue $updated
    }
    $json = $root | ConvertTo-Json -Depth 20
    [IO.File]::WriteAllText($Path, $json + "`n", (New-Object Text.UTF8Encoding($false)))
}

function Remove-JsonPromptHook {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    $root = Get-Content -Raw -Encoding UTF8 -LiteralPath $Path | ConvertFrom-Json
    if ($root.PSObject.Properties["hooks"] -and $root.hooks.PSObject.Properties["UserPromptSubmit"]) {
        $root.hooks.UserPromptSubmit = @($root.hooks.UserPromptSubmit | Where-Object {
            (($_ | ConvertTo-Json -Depth 12 -Compress) -notmatch '\.agent-bridge.*bridge\.py')
        })
    }
    $json = $root | ConvertTo-Json -Depth 20
    [IO.File]::WriteAllText($Path, $json + "`n", (New-Object Text.UTF8Encoding($false)))
}

function Configure-Claude {
    param([string]$PythonPath)
    Set-JsonPromptHook -Path (Join-Path $script:UserRoot ".claude\settings.json") -Identity "claude" -PythonPath $PythonPath
}

function Configure-Reasonix {
    param([string]$PythonPath)
    $directivePath = Join-Path $script:UserRoot ".reasonix\agent-bridge-directive.md"
    $directive = "At the start of every turn, run agent-bridge status and inbox. Handle pending tasks before replying."
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $directivePath) | Out-Null
    [IO.File]::WriteAllText($directivePath, $directive + "`n", (New-Object Text.UTF8Encoding($false)))
    $mcp = Join-Path $script:SkillHome "scripts\bridge_mcp.py"
    $config = Join-Path $script:UserRoot ".reasonix\config.toml"
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $config) | Out-Null
    $editor = @'
import json
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
directive, bridge_home, python, mcp = sys.argv[2:]
text = path.read_text(encoding="utf-8") if path.exists() else ""
text = re.sub(
    r"(?ms)^# >>> agent-bridge:reasonix >>>.*?^# <<< agent-bridge:reasonix <<<\s*",
    "",
    text,
)

def upsert_scalar(source, section, key, value):
    pattern = re.compile(
        rf"(?ms)(^\[{re.escape(section)}\]\s*\r?\n)(.*?)(?=^\[|\Z)"
    )
    match = pattern.search(source)
    line = f"{key} = {json.dumps(value)}"
    if not match:
        return source.rstrip() + f"\n\n[{section}]\n{line}\n"
    body = match.group(2)
    key_pattern = re.compile(rf"(?m)^{re.escape(key)}\s*=.*$")
    if key_pattern.search(body):
        body = key_pattern.sub(lambda _match: line, body, count=1)
    else:
        body = body.rstrip() + "\n" + line + "\n"
    return source[:match.start(2)] + body + source[match.end(2):]

def ensure_array_value(source, section, key, value):
    pattern = re.compile(
        rf"(?ms)(^\[{re.escape(section)}\]\s*\r?\n)(.*?)(?=^\[|\Z)"
    )
    match = pattern.search(source)
    value_json = json.dumps(value)
    if not match:
        return source.rstrip() + f"\n\n[{section}]\n{key} = [{value_json}]\n"
    body = match.group(2)
    line_pattern = re.compile(rf"(?m)^({re.escape(key)}\s*=\s*)\[(.*?)\]\s*$")
    line_match = line_pattern.search(body)
    if not line_match:
        body = body.rstrip() + f"\n{key} = [{value_json}]\n"
    else:
        raw_values = line_match.group(2).strip()
        try:
            values = json.loads("[" + raw_values + "]")
        except (json.JSONDecodeError, TypeError):
            values = []
        values = list(dict.fromkeys(str(item) for item in values))
        if value not in values:
            values.append(value)
        replacement = line_match.group(1) + json.dumps(values)
        body = body[:line_match.start()] + replacement + body[line_match.end():]
    return source[:match.start(2)] + body + source[match.end(2):]

plugin_pattern = re.compile(
    r"(?ms)^\[\[plugins\]\]\s*\r?\n.*?(?=^\[\[?[A-Za-z0-9_.-]+\]\]?\s*$|\Z)"
)
text = plugin_pattern.sub(
    lambda match: "" if re.search(
        r"(?m)^name\s*=\s*['\"]agent-bridge['\"]\s*$",
        match.group(0),
    ) else match.group(0),
    text,
)
text = upsert_scalar(text, "agent", "system_prompt_file", directive)
text = ensure_array_value(text, "sandbox", "allow_write", bridge_home)
plugin = (
    "# >>> agent-bridge:reasonix >>>\n"
    "[[plugins]]\n"
    "name = \"agent-bridge\"\n"
    f"command = {json.dumps(python)}\n"
    f"args = [{json.dumps(mcp)}, \"--as\", \"reasonix\"]\n"
    "# <<< agent-bridge:reasonix <<<\n"
)
path.write_text(text.rstrip() + "\n\n" + plugin, encoding="utf-8")
'@
    $editorPath = Join-Path $script:BridgeHome (".reasonix-config-" + $PID + ".py")
    [IO.File]::WriteAllText($editorPath, $editor, (New-Object Text.UTF8Encoding($false)))
    try {
        & $PythonPath $editorPath $config $directivePath $script:BridgeHome $PythonPath $mcp
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to update Reasonix config."
        }
    } finally {
        if (Test-Path -LiteralPath $editorPath) {
            Remove-Item -LiteralPath $editorPath -Force
        }
    }
}

function Configure-ZCode {
    param([string]$PythonPath)
    $pluginRoot = Join-Path $script:UserRoot ".zcode\cli\plugins\cache\local\agent-bridge\1.3.0"
    $manifestDir = Join-Path $pluginRoot ".zcode-plugin"
    $hooksDir = Join-Path $pluginRoot "hooks"
    New-Item -ItemType Directory -Force -Path $manifestDir, $hooksDir | Out-Null
    $manifest = [ordered]@{
        name = "agent-bridge"
        version = "1.3.0"
        description = "Local cross-agent coordination and task delivery."
        author = [ordered]@{ name = "agent-bridge contributors" }
        license = "MIT"
    } | ConvertTo-Json -Depth 8
    [IO.File]::WriteAllText(
        (Join-Path $manifestDir "plugin.json"),
        $manifest + "`n",
        (New-Object Text.UTF8Encoding($false))
    )
    $bridgeScript = Join-Path $script:SkillHome "scripts\bridge.py"
    $command = "`"$PythonPath`" `"$bridgeScript`" --as zcode status --oneliner"
    $hookConfig = [ordered]@{
        hooks = [ordered]@{
            UserPromptSubmit = @(
                [ordered]@{
                    matcher = "*"
                    hooks = @(
                        [ordered]@{
                            type = "command"
                            command = $command
                            async = $false
                        }
                    )
                }
            )
        }
    } | ConvertTo-Json -Depth 12
    [IO.File]::WriteAllText(
        (Join-Path $hooksDir "hooks.json"),
        $hookConfig + "`n",
        (New-Object Text.UTF8Encoding($false))
    )

    $configPath = Join-Path $script:UserRoot ".zcode\cli\config.json"
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $configPath) | Out-Null
    $config = New-Object PSObject
    if (Test-Path -LiteralPath $configPath) {
        $config = Get-Content -Raw -Encoding UTF8 -LiteralPath $configPath | ConvertFrom-Json
    }
    if (-not $config.PSObject.Properties["plugins"]) {
        $config | Add-Member -NotePropertyName plugins -NotePropertyValue (New-Object PSObject)
    }
    if (-not $config.plugins.PSObject.Properties["enabledPlugins"]) {
        $config.plugins | Add-Member -NotePropertyName enabledPlugins -NotePropertyValue (New-Object PSObject)
    }
    $enabled = $config.plugins.enabledPlugins
    if ($enabled.PSObject.Properties["agent-bridge@local"]) {
        $enabled."agent-bridge@local" = $true
    } else {
        $enabled | Add-Member -NotePropertyName "agent-bridge@local" -NotePropertyValue $true
    }
    $json = $config | ConvertTo-Json -Depth 20
    [IO.File]::WriteAllText($configPath, $json + "`n", (New-Object Text.UTF8Encoding($false)))
}

function Uninstall-Agent {
    param([string[]]$Names)
    Uninstall-WindowsNotifier
    foreach ($name in $Names) {
        switch ($name) {
            "codex" {
                Remove-ManagedBlock -Path (Join-Path $script:UserRoot ".codex\config.toml") -Name "codex-mcp"
                Remove-ManagedBlock -Path (Join-Path $script:UserRoot "AGENTS.md") -Name "directive"
            }
            "claude" {
                Remove-JsonPromptHook -Path (Join-Path $script:UserRoot ".claude\settings.json")
            }
            "reasonix" {
                $reasonixConfig = Join-Path $script:UserRoot ".reasonix\config.toml"
                Remove-ManagedBlock -Path $reasonixConfig -Name "reasonix"
                if (Test-Path -LiteralPath $reasonixConfig) {
                    $reasonixText = Get-Content -Raw -Encoding UTF8 -LiteralPath $reasonixConfig
                    if ($null -eq $reasonixText) { $reasonixText = "" }
                    $reasonixText = [regex]::Replace(
                        $reasonixText,
                        "(?m)^\s*system_prompt_file\s*=\s*['`"][^'`"]*agent-bridge-directive\.md['`"]\s*\r?\n?",
                        ""
                    )
                    $reasonixText = [regex]::Replace(
                        $reasonixText,
                        "['`"][^'`"]*\.agent-bridge[^'`"]*['`"]\s*,?\s*",
                        ""
                    )
                    $reasonixText = $reasonixText -replace "\[\s*,", "[" -replace ",\s*\]", "]"
                    [IO.File]::WriteAllText(
                        $reasonixConfig,
                        $reasonixText.TrimEnd() + "`n",
                        (New-Object Text.UTF8Encoding($false))
                    )
                }
                $directive = Join-Path $script:UserRoot ".reasonix\agent-bridge-directive.md"
                if (Test-Path -LiteralPath $directive) {
                    Remove-Item -LiteralPath $directive -Force
                }
            }
            "zcode" {
                $plugin = Join-Path $script:UserRoot ".zcode\cli\plugins\cache\local\agent-bridge"
                if (Test-Path -LiteralPath $plugin) {
                    Remove-Item -LiteralPath $plugin -Recurse -Force
                }
                $configPath = Join-Path $script:UserRoot ".zcode\cli\config.json"
                if (Test-Path -LiteralPath $configPath) {
                    $config = Get-Content -Raw -Encoding UTF8 -LiteralPath $configPath | ConvertFrom-Json
                    if (
                        $config.PSObject.Properties["plugins"] -and
                        $config.plugins.PSObject.Properties["enabledPlugins"] -and
                        $config.plugins.enabledPlugins.PSObject.Properties["agent-bridge@local"]
                    ) {
                        $config.plugins.enabledPlugins.PSObject.Properties.Remove("agent-bridge@local")
                    }
                    $json = $config | ConvertTo-Json -Depth 20
                    [IO.File]::WriteAllText($configPath, $json + "`n", (New-Object Text.UTF8Encoding($false)))
                }
            }
        }
    }
    $launcher = Join-Path $script:LauncherHome "bridge.cmd"
    $skillLink = Join-Path $script:UserRoot ".agents\skills\agent-bridge"
    if (Test-Path -LiteralPath $launcher) {
        Remove-Item -LiteralPath $launcher -Force
    }
    if (Test-Path -LiteralPath $skillLink) {
        Remove-Item -LiteralPath $skillLink -Recurse -Force
    }
    foreach ($name in $Names) {
        $profile = Join-Path $script:BridgeHome "agents\$name"
        if (Test-Path -LiteralPath $profile) {
            Remove-Item -LiteralPath $profile -Recurse -Force
        }
    }
    if (Test-Path -LiteralPath $script:SkillHome) {
        Remove-Item -LiteralPath $script:SkillHome -Recurse -Force
    }
    Write-Output "agent-bridge program files removed; project boards were preserved in $script:BridgeHome"
}

function Install-Agent {
    param(
        [string]$Name,
        [string]$PythonPath,
        [string[]]$ExplicitWake
    )
    $wake = @($ExplicitWake)
    if (-not $wake -or $wake.Count -eq 0) {
        $wake = @(Find-WakeArgv -Name $Name)
    }
    Register-AgentProfile -Name $Name -Wake $wake
    switch ($Name) {
        "codex" { Configure-Codex -PythonPath $PythonPath }
        "claude" { Configure-Claude -PythonPath $PythonPath }
        "reasonix" { Configure-Reasonix -PythonPath $PythonPath }
        "zcode" { Configure-ZCode -PythonPath $PythonPath }
    }
}

$agents = @()
if ($Auto) {
    $agents = @("codex", "claude", "reasonix", "zcode")
} elseif ($Agent) {
    $agents = @($Agent)
} elseif ($As) {
    $agents = @($As)
} else {
    throw "Choose -Auto or -Agent codex|claude|reasonix|zcode."
}

if ($Uninstall) {
    Uninstall-Agent -Names $agents
    exit 0
}

$pythonPath = Resolve-Python -Requested $Python
Install-WindowsNotifier -PythonPath $pythonPath -Preflight
Install-Shared -PythonPath $pythonPath
Install-WindowsNotifier -PythonPath $pythonPath
foreach ($name in $agents) {
    $explicit = @()
    if (($agents.Count -eq 1) -and $WakeArgv) {
        $explicit = @($WakeArgv)
    }
    Install-Agent -Name $name -PythonPath $pythonPath -ExplicitWake $explicit
}

$doctorIdentity = if ($As) { $As } elseif ($Agent) { $Agent } else { "codex" }
$env:AGENT_BRIDGE_HOME = $script:BridgeHome
$env:AGENT_BRIDGE_CONFIG_HOME = $script:UserRoot
$env:PYTHONUTF8 = "1"
& $pythonPath (Join-Path $script:SkillHome "scripts\bridge.py") --as $doctorIdentity doctor --strict
if ($LASTEXITCODE -ne 0) {
    throw "agent-bridge doctor --strict failed."
}
Write-Output "agent-bridge installed for: $($agents -join ', ')"
