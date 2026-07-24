param([string]$Cargo = "cargo")
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$lock = Join-Path $root 'Cargo.lock'
& $Cargo metadata --manifest-path (Join-Path $root 'Cargo.toml') --locked --format-version 1 | ConvertFrom-Json |
  Select-Object -ExpandProperty packages | Sort-Object name,version | ForEach-Object { "{0} {1} {2}" -f $_.name,$_.version,$_.license } |
  Set-Content -Encoding utf8 (Join-Path $root 'DEPENDENCIES.txt')
$exe = Get-ChildItem (Join-Path $root 'target') -Recurse -Filter 'agent-bridge-windows-notify.exe' |
  Where-Object { $_.FullName -match '\\release\\' } | Select-Object -First 1
if ($null -eq $exe) { throw 'release helper not found' }
if ($exe.Length -gt 5MB) { throw "release helper exceeds 5 MiB: $($exe.Length)" }
"release_bytes=$($exe.Length)" | Set-Content -Encoding ascii (Join-Path $root 'RELEASE_SIZE.txt')
