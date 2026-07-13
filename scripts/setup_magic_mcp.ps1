# Настройка 21st.dev Magic MCP для Cursor (локально, ключ не в git)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "21st.dev Magic MCP setup"
Write-Host "Get API key: https://21st.dev/magic"
$key = Read-Host "Paste TWENTY_FIRST / Magic API key (input hidden)"
if ([string]::IsNullOrWhiteSpace($key)) {
    Write-Error "API key is required."
}

Write-Host "Installing MCP for Cursor via @21st-dev/cli..."
npx @21st-dev/cli@latest install cursor --api-key $key

Write-Host ""
Write-Host "Done. Restart Cursor and verify MCP '@21st-dev/magic' in Customize."
Write-Host "Submodule reference: vendor/magic-mcp/"
