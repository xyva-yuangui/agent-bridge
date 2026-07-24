param(
  [string]$Cargo = "cargo",
  [string]$Target = "x86_64-pc-windows-gnu"
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
$dist = Get-Item (Join-Path $root "dist\windows-x86_64\agent-bridge-windows-notify.exe")
$metadataPath = Join-Path $root "dist\windows-x86_64\build.json"
$metadata = Get-Content -Raw -Encoding UTF8 -LiteralPath $metadataPath | ConvertFrom-Json
$metadataNames = @($metadata.PSObject.Properties.Name | Sort-Object)
$expectedMetadataNames = @(
  "artifact", "bytes", "cargo_lock_sha256", "rustc", "schema", "sha256", "target"
) | Sort-Object
$builtHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $exe.FullName).Hash
$distHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $dist.FullName).Hash
$lockHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $lock).Hash
if ($builtHash -ine $distHash -or $distHash -ine [string]$metadata.sha256) {
  throw "dist helper hash does not match the freshly built release"
}
if (
  ($metadataNames -join "`n") -ne ($expectedMetadataNames -join "`n") -or
  [int]$metadata.schema -ne 1 -or
  [string]$metadata.artifact -ne $dist.Name -or
  [string]$metadata.target -ne $Target -or
  [string]$metadata.rustc -notmatch '^\d+\.\d+\.\d+$' -or
  [long]$metadata.bytes -ne $dist.Length -or
  [string]$metadata.cargo_lock_sha256 -ine $lockHash
) {
  throw "dist build metadata is invalid"
}
"release_bytes=$($exe.Length)" | Set-Content -Encoding ascii (Join-Path $root 'RELEASE_SIZE.txt')
