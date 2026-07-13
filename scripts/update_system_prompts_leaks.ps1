# Обновить vendor/system_prompts_leaks (git submodule)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
Write-Host "Updating system_prompts_leaks submodule..."
git submodule update --init --remote vendor/system_prompts_leaks
Write-Host "Done. See vendor/system_prompts_leaks/README.md"
