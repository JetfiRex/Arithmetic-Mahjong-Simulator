param([ValidateRange(1024, 65535)][int]$Port = 8771)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Create the project .venv and install multiplayer/requirements.txt first. See README.md.'
}
Push-Location -LiteralPath $Root
try {
    Write-Host 'Fresh opening. Ctrl+C stops the game; the next start is empty. Cloudflare is not started.'
    & $Python -m multiplayer.web --port $Port
    if ($LASTEXITCODE -notin @(0, 130, -1073741510)) { throw "Server exited with code $LASTEXITCODE" }
} finally {
    Pop-Location
}
