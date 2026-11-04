param([switch]$SkipTests)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root
$Python = if ($env:CHIMERA_PYTHON) { $env:CHIMERA_PYTHON } else { "python" }
$Stage = Join-Path $Root ".build\dist"
$Final = Join-Path $Root "dist\Chimera"
if (-not $SkipTests) { & $Python -m pytest -q; if ($LASTEXITCODE) { exit $LASTEXITCODE } }
& $Python -m compileall -q chimera tests; if ($LASTEXITCODE) { exit $LASTEXITCODE }
& $Python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE) { throw "PyInstaller is not installed. Install it with: $Python -m pip install pyinstaller" }
New-Item -ItemType Directory -Force -Path $Stage | Out-Null
$FletArchive = Join-Path $Root ".build\flet-desktop\flet-windows.zip"
New-Item -ItemType Directory -Force -Path (Split-Path $FletArchive) | Out-Null
& $Python (Join-Path $Root "scripts\prepare_flet_client.py") --output $FletArchive
if ($LASTEXITCODE) { throw "Flet desktop client preparation failed" }
if (-not (Test-Path $FletArchive)) { throw "Flet desktop client archive was not produced" }

$env:CHIMERA_FLET_CLIENT_ARCHIVE = $FletArchive
try {
    & $Python -m PyInstaller --noconfirm --clean --distpath $Stage --workpath (Join-Path $Root ".build\work") (Join-Path $Root "chimera.spec")
    if ($LASTEXITCODE) { throw "PyInstaller failed" }
}
finally {
    Remove-Item Env:CHIMERA_FLET_CLIENT_ARCHIVE -ErrorAction SilentlyContinue
}
$Exe = Join-Path $Stage "Chimera\Chimera.exe"
if (-not (Test-Path $Exe)) { throw "Expected executable missing: $Exe" }
if ((Get-Item $Exe).Length -le 0) { throw "Packaged executable is empty" }
New-Item -ItemType Directory -Force -Path (Split-Path $Final) | Out-Null
if (Test-Path $Final) { Remove-Item $Final -Recurse -Force }
Move-Item (Join-Path $Stage "Chimera") $Final
$AbsoluteExe = (Resolve-Path (Join-Path $Final "Chimera.exe")).Path
$PortableZip = Join-Path $Root "dist\Chimera-Windows-portable.zip"
if (Test-Path $PortableZip) { Remove-Item $PortableZip -Force }
Compress-Archive -Path (Join-Path $Final "*") -DestinationPath $PortableZip -CompressionLevel Optimal
if (-not (Test-Path $PortableZip)) { throw "Portable ZIP was not created: $PortableZip" }
if ((Get-Item $PortableZip).Length -le 0) { throw "Portable ZIP is empty: $PortableZip" }
Write-Host "BUILD SUCCESS: $AbsoluteExe"
Write-Host "PORTABLE ZIP: $PortableZip"
