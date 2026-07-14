#Requires -Version 5.1
<#
.SYNOPSIS
  Register SubHub in Windows "Apps & features" / Programs and Features (HKCU, no admin).
#>
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Split-Path -Parent $PSScriptRoot)).Path
$Exe = Join-Path $Root "SubHub.exe"
$UninstallPs1 = Join-Path $PSScriptRoot "uninstall_subhub.ps1"
$VerFile = Join-Path $Root "VERSION"
$Version = "1.4.0"
if (Test-Path $VerFile) {
  $Version = ((Get-Content $VerFile -TotalCount 1 -ErrorAction SilentlyContinue) | Out-String).Trim()
  if (-not $Version) { $Version = "1.4.0" }
}

$UninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\CrownfallSubHub"
New-Item -Path $UninstallKey -Force | Out-Null

$UninstallCmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$UninstallPs1`""
$DisplayIcon = if (Test-Path $Exe) { "$Exe,0" } else { "$Root\assets\app.ico,0" }

Set-ItemProperty -Path $UninstallKey -Name "DisplayName" -Value "SubHub"
Set-ItemProperty -Path $UninstallKey -Name "DisplayVersion" -Value $Version
Set-ItemProperty -Path $UninstallKey -Name "Publisher" -Value "Crownfall"
Set-ItemProperty -Path $UninstallKey -Name "InstallLocation" -Value $Root
Set-ItemProperty -Path $UninstallKey -Name "DisplayIcon" -Value $DisplayIcon
Set-ItemProperty -Path $UninstallKey -Name "UninstallString" -Value $UninstallCmd
Set-ItemProperty -Path $UninstallKey -Name "QuietUninstallString" -Value $UninstallCmd
Set-ItemProperty -Path $UninstallKey -Name "NoModify" -Value 1 -Type DWord
Set-ItemProperty -Path $UninstallKey -Name "NoRepair" -Value 1 -Type DWord
Set-ItemProperty -Path $UninstallKey -Name "EstimatedSize" -Value 120000 -Type DWord

Write-Host "OK: registered uninstall -> $UninstallKey"
Write-Host "Settings -> Apps -> SubHub  or  Control Panel -> Programs and Features"
