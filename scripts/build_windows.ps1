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
& $Python -m PyInstaller --noconfirm --clean --distpath $Stage --workpath (Join-Path $Root ".build\work") (Join-Path $Root "chimera.spec")
if ($LASTEXITCODE) { throw "PyInstaller failed" }
$Exe = Join-Path $Stage "Chimera\Chimera.exe"
if (-not (Test-Path $Exe)) { throw "Expected executable missing: $Exe" }
if ((Get-Item $Exe).Length -le 0) { throw "Packaged executable is empty" }
New-Item -ItemType Directory -Force -Path (Split-Path $Final) | Out-Null
if (Test-Path $Final) { Remove-Item $Final -Recurse -Force }
Move-Item (Join-Path $Stage "Chimera") $Final
$AbsoluteExe = (Resolve-Path (Join-Path $Final "Chimera.exe")).Path
Write-Host "BUILD SUCCESS: $AbsoluteExe"
