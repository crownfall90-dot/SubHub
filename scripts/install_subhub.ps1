#Requires -Version 5.1
<#
.SYNOPSIS
  Portable install: Start Menu + Desktop shortcuts + Apps & Features uninstall entry.
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $Root "SubHub.exe"))) {
  Write-Host "Building SubHub.exe..."
  & cmd /c "`"$PSScriptRoot\build_subhub_exe.bat`""
}
$Exe = Join-Path $Root "SubHub.exe"
if (-not (Test-Path $Exe)) { throw "SubHub.exe not found. Run scripts\build_subhub_exe.bat" }

$Ico = Join-Path $Root "assets\app.ico"
if (-not (Test-Path $Ico)) { $Ico = $Exe }

function New-Shortcut($Path, $Target, $WorkDir, $Icon) {
  $dir = Split-Path $Path -Parent
  if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  $w = New-Object -ComObject WScript.Shell
  $s = $w.CreateShortcut($Path)
  $s.TargetPath = $Target
  $s.WorkingDirectory = $WorkDir
  $s.Description = "SubHub"
  $s.IconLocation = "$Icon,0"
  $s.Save()
}

$StartMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\SubHub.lnk"
$Desktop = Join-Path ([Environment]::GetFolderPath("Desktop")) "SubHub.lnk"
New-Shortcut $StartMenu $Exe $Root $Ico
New-Shortcut $Desktop $Exe $Root $Ico

& "$PSScriptRoot\register_subhub_uninstall.ps1"

Write-Host "OK: Start Menu -> $StartMenu"
Write-Host "OK: Desktop -> $Desktop"
Write-Host "OK: Apps & Features -> SubHub (удалить можно оттуда)"
Write-Host ""
Write-Host "Закрепить: Пуск -> Все приложения -> SubHub -> ПКМ -> Закрепить"
Write-Host "Инсталлятор Inno: scripts\build_installer.bat (если установлен Inno Setup)"
