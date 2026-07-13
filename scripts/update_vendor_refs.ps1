# Обновить vendor/system_prompts_leaks и пересобрать INDEX MotionSites
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
Write-Host "Updating system_prompts_leaks submodule..."
git submodule update --init --remote vendor/system_prompts_leaks
Write-Host "Regenerating motionsites INDEX..."
python scripts/motionsites_index.py
Write-Host "Done."
