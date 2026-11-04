param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA "Programs\Chimera\Ghidra")
)

$ErrorActionPreference = "Stop"
$ApiUrl = "https://api.github.com/repos/NationalSecurityAgency/ghidra/releases/latest"
$Headers = @{
    Accept = "application/vnd.github+json"
    "User-Agent" = "Chimera-Windows-Setup"
}

Write-Host ""
Write-Host "CHIMERA / GHIDRA INSTALLER" -ForegroundColor Cyan
Write-Host "Fetching the latest official Ghidra release metadata..."

try {
    $Release = Invoke-RestMethod -Uri $ApiUrl -Headers $Headers -Method Get
}
catch {
    throw "Could not reach the official Ghidra release API. Install manually from https://github.com/NationalSecurityAgency/ghidra/releases . $($_.Exception.Message)"
}

$Asset = $Release.assets |
    Where-Object {
        $_.name -match '^ghidra_.*_PUBLIC_.*\.zip$' -and
        $_.browser_download_url -match '^https://github\.com/NationalSecurityAgency/ghidra/releases/download/'
    } |
    Select-Object -First 1

if (-not $Asset) {
    throw "No official pre-built Ghidra ZIP was found in release $($Release.tag_name). Open https://github.com/NationalSecurityAgency/ghidra/releases and install the PUBLIC ZIP manually."
}

$SafeTag = [regex]::Replace([string]$Release.tag_name, '[^A-Za-z0-9._-]', '_')
$Destination = Join-Path $InstallRoot $SafeTag
$Headless = Join-Path $Destination "support\analyzeHeadless.bat"

if (Test-Path $Headless) {
    Write-Host "Ghidra is already installed at $Destination"
}
else {
    $TempRoot = Join-Path $env:TEMP ("chimera-ghidra-" + [guid]::NewGuid().ToString("N"))
    $Archive = Join-Path $TempRoot $Asset.name
    $Extract = Join-Path $TempRoot "extracted"
    New-Item -ItemType Directory -Force -Path $TempRoot, $Extract | Out-Null

    try {
        Write-Host "Downloading $($Asset.name)..."
        Invoke-WebRequest -Uri $Asset.browser_download_url -OutFile $Archive -Headers @{ "User-Agent" = "Chimera-Windows-Setup" }
        if (-not (Test-Path $Archive) -or (Get-Item $Archive).Length -lt 1MB) {
            throw "The downloaded Ghidra archive is missing or unexpectedly small."
        }

        Write-Host "Extracting Ghidra..."
        Expand-Archive -LiteralPath $Archive -DestinationPath $Extract -Force
        $HeadlessAsset = Get-ChildItem -LiteralPath $Extract -Filter "analyzeHeadless.bat" -File -Recurse |
            Where-Object { $_.Directory.Name -eq "support" } |
            Select-Object -First 1
        if (-not $HeadlessAsset) {
            throw "The official archive did not contain support\analyzeHeadless.bat."
        }

        $ExtractedRoot = Split-Path -Parent (Split-Path -Parent $HeadlessAsset.FullName)
        New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
        if (Test-Path $Destination) {
            throw "The target directory already exists but does not contain a working Ghidra install: $Destination"
        }
        Move-Item -LiteralPath $ExtractedRoot -Destination $Destination
    }
    finally {
        if (Test-Path $TempRoot) {
            Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

if (-not (Test-Path $Headless)) {
    throw "Ghidra install validation failed: $Headless"
}

[Environment]::SetEnvironmentVariable("GHIDRA_HOME", $Destination, "User")
$env:GHIDRA_HOME = $Destination

$JdkCandidates = @()
$AdoptiumRoot = Join-Path $env:ProgramFiles "Eclipse Adoptium"
if (Test-Path $AdoptiumRoot) {
    $JdkCandidates += Get-ChildItem -LiteralPath $AdoptiumRoot -Directory -Filter "jdk-25*" |
        ForEach-Object { Join-Path $_.FullName "bin\java.exe" }
}
if ($env:JAVA_HOME) {
    $JdkCandidates += Join-Path $env:JAVA_HOME "bin\java.exe"
}
$Jdk25 = $null
foreach ($Candidate in $JdkCandidates) {
    if (-not (Test-Path $Candidate)) { continue }
    $VersionOutput = (& $Candidate -version 2>&1 | Out-String)
    if ($VersionOutput -match '(?i)(?:version\s+["'']?|openjdk\s+)25(?:[.\s"'']|$)') {
        $Jdk25 = $Candidate
        break
    }
}

Write-Host ""
Write-Host "Ghidra installed: $Destination" -ForegroundColor Green
Write-Host "GHIDRA_HOME registered for the current Windows user." -ForegroundColor Green
if (-not $Jdk25) {
    Write-Warning "Ghidra 12.2 requires a 64-bit JDK 25+. Install Eclipse Temurin 25, then restart Chimera."
}
else {
    $JavaHome = Split-Path -Parent (Split-Path -Parent $Jdk25)
    [Environment]::SetEnvironmentVariable("JAVA_HOME", $JavaHome, "User")
    $env:JAVA_HOME = $JavaHome
    Write-Host "JDK 25 found: $Jdk25" -ForegroundColor Green
}
Write-Host "Close and reopen Chimera to refresh the application inventory."
