param(
  [string]$Cargo = "cargo",
  [string]$Target = "x86_64-pc-windows-msvc"
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$lock = Join-Path $root 'Cargo.lock'
& $Cargo build --manifest-path (Join-Path $root 'Cargo.toml') --locked --release --target $Target
if ($LASTEXITCODE -ne 0) { throw 'locked release build failed' }
& $Cargo metadata --manifest-path (Join-Path $root 'Cargo.toml') --locked --format-version 1 | ConvertFrom-Json |
  Select-Object -ExpandProperty packages | Sort-Object name,version | ForEach-Object { "{0} {1} {2}" -f $_.name,$_.version,$_.license } |
  Set-Content -Encoding utf8 (Join-Path $root 'DEPENDENCIES.txt')
$exe = Get-Item (Join-Path $root "target\$Target\release\agent-bridge-windows-notify.exe")
if ($exe.Length -gt 5MB) { throw "release helper exceeds 5 MiB: $($exe.Length)" }
$distDirectory = Join-Path $root "dist\windows-x86_64"
New-Item -ItemType Directory -Force $distDirectory | Out-Null
$distPath = Join-Path $distDirectory "agent-bridge-windows-notify.exe"
Copy-Item -LiteralPath $exe.FullName -Destination $distPath -Force
$dist = Get-Item $distPath
$metadataPath = Join-Path $root "dist\windows-x86_64\build.json"
$distHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $dist.FullName).Hash
$lockHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $lock).Hash
$rustcVersion = (& rustc --version --verbose | Select-String '^release:' | ForEach-Object { $_.Line.Split(':', 2)[1].Trim() })
if ($rustcVersion -notmatch '^\d+\.\d+\.\d+$') { throw "rustc release version is unavailable" }
[ordered]@{
  schema = 1
  artifact = $dist.Name
  target = $Target
  rustc = $rustcVersion
  bytes = $dist.Length
  sha256 = $distHash
  cargo_lock_sha256 = $lockHash
} | ConvertTo-Json | Set-Content -Encoding utf8 $metadataPath
"release_bytes=$($exe.Length)" | Set-Content -Encoding ascii (Join-Path $root 'RELEASE_SIZE.txt')
