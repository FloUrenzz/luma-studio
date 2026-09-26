$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$pythonExe = $null

foreach ($candidate in @($bundledPython, 'python', 'py')) {
    try {
        & $candidate -c 'from PIL import Image' 2>$null
        if ($LASTEXITCODE -eq 0) {
            $pythonExe = $candidate
            break
        }
    } catch { }
}

if (-not $pythonExe) {
    Write-Host 'Не найден Python с Pillow. Установите Python 3.10+ и выполните:' -ForegroundColor Yellow
    Write-Host '  pip install -r requirements.txt'
    exit 1
}

Push-Location $projectRoot
try {
    Write-Host 'Luma Studio запускается на http://127.0.0.1:8765' -ForegroundColor Cyan
    Write-Host 'Для остановки нажмите Ctrl+C.'
    & $pythonExe 'server.py'
} finally {
    Pop-Location
}
