#Requires -Version 5.1
<#
.SYNOPSIS
  Stage SubHub files for installer payload (no secrets / profiles / caches).
#>
param(
  [string]$StageDir = "",
  [string]$ZipPath = ""
)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Split-Path -Parent $PSScriptRoot)).Path
if (-not $StageDir) { $StageDir = Join-Path $Root "dist\stage" }
if (-not $ZipPath) { $ZipPath = Join-Path $Root "dist\payload.zip" }

if (-not (Test-Path (Join-Path $Root "SubHub.exe"))) {
  & cmd /c "`"$PSScriptRoot\build_subhub_exe.bat`""
}
if (-not (Test-Path (Join-Path $Root "SubHub.exe"))) {
  throw "SubHub.exe missing"
}

if (Test-Path $StageDir) { Remove-Item $StageDir -Recurse -Force }
New-Item -ItemType Directory -Path $StageDir -Force | Out-Null
New-Item -ItemType Directory -Path (Split-Path $ZipPath -Parent) -Force | Out-Null

function Copy-ItemSafe($src, $dst) {
  if (-not (Test-Path $src)) { return }
  $parent = Split-Path $dst -Parent
  if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
  Copy-Item -LiteralPath $src -Destination $dst -Recurse -Force
}

# Core launcher + Python app
Copy-ItemSafe (Join-Path $Root "SubHub.exe") (Join-Path $StageDir "SubHub.exe")
Get-ChildItem $Root -Filter "*.py" -File | ForEach-Object {
  Copy-ItemSafe $_.FullName (Join-Path $StageDir $_.Name)
}

# Packages / assets needed at runtime
foreach ($name in @(
  "ggsell", "assets", "veepn_extension", "vpn_extension",
  "VERSION", "LICENSE", "requirements.txt", "CHANGELOG.md"
)) {
  Copy-ItemSafe (Join-Path $Root $name) (Join-Path $StageDir $name)
}

# Лаунчер SubHub.exe ищет scripts\_gui_boot.py (обёртка с crash-логом)
Copy-ItemSafe (Join-Path $Root "scripts\_gui_boot.py") (Join-Path $StageDir "scripts\_gui_boot.py")

# Config templates only — never ship the builder's live config.yaml (API keys).
# Fresh install gets placeholders; upgrade keeps existing config/secrets via C# only-if-missing.
$exampleCfg = Join-Path $Root "config.yaml.example"
if (-not (Test-Path $exampleCfg)) { $exampleCfg = Join-Path $Root "config.example.yaml" }
$liveCfg = Join-Path $Root "config.yaml"
$stagedCfg = Join-Path $StageDir "config.yaml"
if (Test-Path $exampleCfg) {
  Copy-ItemSafe $exampleCfg $stagedCfg
  Copy-ItemSafe $exampleCfg (Join-Path $StageDir "config.yaml.example")
} elseif (Test-Path $liveCfg) {
  # Fallback: redact known secret-shaped keys from a shallow YAML copy
  $raw = Get-Content -LiteralPath $liveCfg -Raw -Encoding UTF8
  $raw = [regex]::Replace($raw, '(?m)^(\s*(?:api_key|token|password|secret|api_token)\s*:\s*).*$', '${1}""')
  Set-Content -LiteralPath $stagedCfg -Value $raw -Encoding UTF8
  Write-Warning "config.yaml.example missing — staged redacted config.yaml"
} else {
  Set-Content -LiteralPath $stagedCfg -Value "# SubHub config — fill in Settings or edit this file`n" -Encoding UTF8
}
$exSecrets = Join-Path $Root "secrets.yaml.example"
if (-not (Test-Path $exSecrets)) { $exSecrets = Join-Path $Root "secrets.example.yaml" }
Copy-ItemSafe $exSecrets (Join-Path $StageDir "secrets.yaml.example")

# Fresh user data defaults: no always-on background
$data = Join-Path $StageDir "data"
New-Item -ItemType Directory -Path $data -Force | Out-Null
$settings = @{
  background_mode   = $false
  minimize_to_tray  = $false
  run_at_startup    = $false
  start_minimized   = $false
  notify_ggs_orders = $true
  notify_ggs_messages = $true
} | ConvertTo-Json
Set-Content -Path (Join-Path $data "app_settings.json") -Value $settings -Encoding UTF8
Set-Content -Path (Join-Path $StageDir ".installed") -Value "Crownfall SubHub" -Encoding UTF8

# Drop caches from staged packages
Get-ChildItem $StageDir -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($StageDir, $ZipPath, "Optimal", $false)

$bytes = (Get-Item $ZipPath).Length
Write-Host "OK stage: $StageDir"
Write-Host "OK zip:   $ZipPath ($([math]::Round($bytes/1MB,1)) MB)"
