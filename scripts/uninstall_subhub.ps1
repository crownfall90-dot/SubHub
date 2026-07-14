#Requires -Version 5.1
<#
.SYNOPSIS
  Uninstall SubHub from Windows: remove shortcuts + Apps & Features entry.
  Does NOT delete the project folder by default (portable copy may be the only files).
  Pass -RemoveFiles to also delete SubHub.exe launcher + Start Menu leftovers.
#>
param(
  [switch]$RemoveFiles,
  [switch]$Quiet
)
$ErrorActionPreference = "Continue"
$Root = (Resolve-Path (Split-Path -Parent $PSScriptRoot)).Path

function Remove-Lnk($path) {
  if (Test-Path $path) {
    Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    Write-Host "removed: $path"
  }
}

$StartMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\SubHub.lnk"
$Desktop = Join-Path ([Environment]::GetFolderPath("Desktop")) "SubHub.lnk"
$Startup = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\SubHub.lnk"

Remove-Lnk $StartMenu
Remove-Lnk $Desktop
Remove-Lnk $Startup

# Also clear Windows startup via settings file if present
$AppSettings = Join-Path $Root "data\app_settings.json"
if (Test-Path $AppSettings) {
  try {
    $j = Get-Content $AppSettings -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($j.PSObject.Properties.Name -contains "run_at_startup") {
      $j.run_at_startup = $false
      ($j | ConvertTo-Json -Depth 6) | Set-Content $AppSettings -Encoding UTF8
    }
  } catch {}
}

$UninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\CrownfallSubHub"
if (Test-Path $UninstallKey) {
  Remove-Item -Path $UninstallKey -Recurse -Force -ErrorAction SilentlyContinue
  Write-Host "removed registry: $UninstallKey"
}

if ($RemoveFiles) {
  $Exe = Join-Path $Root "SubHub.exe"
  if (Test-Path $Exe) {
    Remove-Item -LiteralPath $Exe -Force -ErrorAction SilentlyContinue
    Write-Host "removed: $Exe"
  }
}

if (-not $Quiet) {
  Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
  [System.Windows.Forms.MessageBox]::Show(
    "SubHub удалён из меню Пуск, рабочего стола и списка программ Windows.`n`nПапка проекта не удалена:`n$Root`n`nУдалите её вручную, если больше не нужна.",
    "SubHub — удаление",
    "OK",
    "Information"
  ) | Out-Null
}

Write-Host "OK: SubHub uninstalled from Windows (folder kept: $Root)"
exit 0
