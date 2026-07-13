# Обновить vendor submodules + Cursor skills (system_prompts, ui-ux-pro-max, motionsites INDEX)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
Write-Host "Updating system_prompts_leaks..."
git submodule update --init --remote vendor/system_prompts_leaks
Write-Host "Updating ui-ux-pro-max-skill..."
git submodule update --init --remote vendor/ui-ux-pro-max-skill
Write-Host "Updating magic-mcp..."
git submodule update --init --remote vendor/magic-mcp
Write-Host "Refreshing UI/UX Pro Max Cursor skill..."
npx -y ui-ux-pro-max-cli init --ai cursor
Write-Host "Regenerating motionsites INDEX..."
python scripts/motionsites_index.py
Write-Host "Done."
