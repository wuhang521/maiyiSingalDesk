$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& node (Join-Path $PSScriptRoot 'scripts\knowledge-local.mjs')
exit $LASTEXITCODE
