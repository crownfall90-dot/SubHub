# Импорт промпта MotionSites из буфера обмена
param(
    [Parameter(Mandatory)][string]$Slug,
    [string]$Title = "",
    [string]$Category = "Hero",
    [string]$Tier = "premium"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dir = Join-Path $root "vendor\motionsites_prompts"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
if (-not $Title) { $Title = ($Slug -replace '-', ' ') }
Write-Host "Вставьте промпт MotionSites и нажмите Enter, затем Ctrl+Z Enter (Windows):"
$lines = @()
while ($true) {
    $line = Read-Host
    if ($null -eq $line) { break }
    $lines += $line
}
$body = ($lines -join "`n").Trim()
if ($body.Length -lt 50) { throw "Слишком короткий текст" }
$url = "https://motionsites.ai/?prompt=$Slug"
$date = Get-Date -Format "yyyy-MM-dd"
$md = @"
---
title: $Title
slug: $Slug
source: $url
tier: $Tier
category: $Category
fetched: $date
---

# $Title

Источник: [MotionSites]($url)

## Prompt

$body
"@
$out = Join-Path $dir "$Slug.md"
Set-Content -Path $out -Value $md -Encoding UTF8
Write-Host "Saved: $out"
