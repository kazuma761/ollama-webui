# Windows equivalent of run.sh: starts the backend, which also serves the frontend at http://127.0.0.1:8000
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot 'backend')

$port = $env:PORT
if (-not $port -and (Test-Path .env)) {
    $port = (Select-String -Path .env -Pattern '^PORT=(.+)$' | Select-Object -First 1).Matches.Groups[1].Value
}
if (-not $port) { $port = 8000 }
if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "Port $port is already in use - the app is probably already running at http://127.0.0.1:$port"
    exit 1
}

# --inexact keeps optional packages (the Von router) installed by `uv sync --extra router`.
uv run --inexact python -m app @args
exit $LASTEXITCODE
