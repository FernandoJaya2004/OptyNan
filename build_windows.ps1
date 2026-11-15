param([switch]$SkipInstall)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$buildPython = Join-Path $PSScriptRoot '.venv-build\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $buildPython)) {
    python -m venv .venv-build
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear el entorno de construcción.' }
}
if (-not $SkipInstall) {
    & $buildPython -m pip install -r requirements-build.txt
    if ($LASTEXITCODE -ne 0) { throw 'No se pudieron instalar las dependencias.' }
}
& $buildPython -m unittest -v
if ($LASTEXITCODE -ne 0) { throw 'Las pruebas fallaron; no se generará el ejecutable.' }
& $buildPython -m PyInstaller --noconfirm --onedir --windowed --noupx --name OptyNan --distpath dist --workpath build --specpath build OptyNan.py
if ($LASTEXITCODE -ne 0) { throw 'Falló la generación del ejecutable.' }
Copy-Item -LiteralPath LEEME-PORTABLE.txt -Destination dist\OptyNan\LEEME.txt -Force
Copy-Item -LiteralPath README.md -Destination dist\OptyNan\README.md -Force
Compress-Archive -LiteralPath dist\OptyNan -DestinationPath dist\OptyNan-Windows-x64.zip -Force
$packageHash = (Get-FileHash -LiteralPath dist\OptyNan-Windows-x64.zip -Algorithm SHA256).Hash.ToLowerInvariant()
"$packageHash  OptyNan-Windows-x64.zip" | Set-Content -LiteralPath dist\SHA256SUMS.txt -Encoding ascii
Write-Host 'Listo: dist\OptyNan\OptyNan.exe'
Write-Host 'Para compartir: dist\OptyNan-Windows-x64.zip'
