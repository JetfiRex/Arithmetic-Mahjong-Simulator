param([ValidateRange(1024, 65535)][int]$Port = 8772, [switch]$NoModels)
$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Create .venv and install singleplayer/requirements.txt first. See README.md.'
}
Push-Location -LiteralPath $ProjectRoot
try {
    $LaunchArgs = @('-m', 'singleplayer', '--port', $Port)
    if ($NoModels) { $LaunchArgs += '--no-models' }
    & $Python @LaunchArgs
    if ($LASTEXITCODE -notin @(0, 130, -1073741510)) { throw "Single-player exited with code $LASTEXITCODE" }
} finally {
    Pop-Location
}
