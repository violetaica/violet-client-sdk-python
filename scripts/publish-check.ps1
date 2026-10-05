#Requires -Version 5.1
<#
.SYNOPSIS
  Packaging readiness gate for violet-sdk (client SDK).

.DESCRIPTION
  Validates that:
    1. The wheel builds from a clean tree
    2. Wheel METADATA declares Name: violet-sdk
    3. Version is single-sourced from violet/__init__.py (no pyproject drift)
    4. Import name is violet (dist name != import name)
    5. pip install from the local wheel works
    6. Offline / network-free tests pass
    7. twine check passes

  Does NOT upload to PyPI and uses NO credentials. It is a readiness gate, not an
  uploader. See "Publish commands" at the end of a successful run.

.PARAMETER SkipTests
  Skip the offline test suite (build + install + metadata checks only).

.PARAMETER KeepWorkDir
  Leave the temp venv/dist directory in place for inspection.

.PARAMETER SkipPyPIProbe
  Skip the (best-effort, non-fatal) check of what is already live on PyPI.
#>
[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$KeepWorkDir,
    [switch]$SkipPyPIProbe
)

$ErrorActionPreference = "Stop"

# Distribution (PyPI) name — NOT the import name (violet).
$DistName = "violet-sdk"
# Wheel filenames normalize '-' to '_'.
$WheelGlob = "violet_sdk-*.whl"

function Write-Step([string]$Message) {
    Write-Host "`n=== $Message ===" -ForegroundColor Cyan
}

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) {
        throw "FAIL: $Message"
    }
    Write-Host "OK: $Message" -ForegroundColor Green
}

function Get-WheelMetadata([string]$WheelPath) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem | Out-Null
    $zip = [System.IO.Compression.ZipFile]::OpenRead($WheelPath)
    try {
        $entry = $zip.Entries | Where-Object { $_.FullName -like "*.dist-info/METADATA" } | Select-Object -First 1
        if (-not $entry) {
            throw "no dist-info/METADATA in $WheelPath"
        }
        $reader = New-Object System.IO.StreamReader($entry.Open())
        try {
            return $reader.ReadToEnd()
        } finally {
            $reader.Dispose()
        }
    } finally {
        $zip.Dispose()
    }
}

function Get-WheelEntries([string]$WheelPath) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem | Out-Null
    $zip = [System.IO.Compression.ZipFile]::OpenRead($WheelPath)
    try {
        return @($zip.Entries | ForEach-Object { $_.FullName })
    } finally {
        $zip.Dispose()
    }
}

function Get-SourceVersion([string]$InitPy) {
    $line = Select-String -Path $InitPy -Pattern '^__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
    if (-not $line) {
        throw "no __version__ in $InitPy"
    }
    return $line.Matches[0].Groups[1].Value
}

# True only if the [project] TABLE carries a static `version = ...` key.
# Must be section-aware: `path = ...` under [tool.hatch.version] is the
# single-sourcing machinery, not drift.
function Test-StaticProjectVersion([string]$Pyproject) {
    $section = ""
    foreach ($line in (Get-Content -LiteralPath $Pyproject)) {
        $trimmed = $line.Trim()
        if ($trimmed -match '^\[([^\]]+)\]') {
            $section = $Matches[1]
            continue
        }
        if ($section -eq "project" -and $trimmed -match '^version\s*=') {
            return $true
        }
    }
    return $false
}

function Clear-BuildArtifacts([string]$Dir) {
    foreach ($name in @("build", "dist")) {
        $p = Join-Path $Dir $name
        if (Test-Path $p) {
            Remove-Item -Recurse -Force $p
        }
    }
    Get-ChildItem -Path $Dir -Filter "*.egg-info" -Directory -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item -Recurse -Force $_.FullName }
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Pyproject = Join-Path $RepoRoot "pyproject.toml"
$InitPy = Join-Path $RepoRoot "violet\__init__.py"

Assert-True (Test-Path $Pyproject) "pyproject exists: $Pyproject"
Assert-True (Test-Path $InitPy) "__init__.py exists: $InitPy"

$SrcVersion = Get-SourceVersion $InitPy
Write-Host "Source version: $DistName=$SrcVersion"

$PythonCandidates = @(
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    (Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
) | Where-Object { $_ -and (Test-Path $_) }

$Python = $null
foreach ($candidate in $PythonCandidates) {
    & $candidate -c "import pip" 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $Python = $candidate
        break
    }
}
if (-not $Python) {
    throw "No Python with pip found. Install Python 3.11+ or 3.12 with pip."
}

Write-Host "Using Python: $Python"
& $Python -c "import sys; print(sys.version)"

$WorkDir = Join-Path ([System.IO.Path]::GetTempPath()) ("violet-sdk-publish-check-" + [guid]::NewGuid().ToString("n"))
New-Item -ItemType Directory -Path $WorkDir | Out-Null
$DistDir = Join-Path $WorkDir "dist"
$VenvDir = Join-Path $WorkDir "venv"
New-Item -ItemType Directory -Path $DistDir | Out-Null

try {
    Write-Step "Install build tooling"
    & $Python -m pip install --upgrade pip build twine | Out-Host

    Write-Step "Purge stale build artifacts"
    Clear-BuildArtifacts $RepoRoot
    Write-Host "OK: build/ dist/ *.egg-info cleared" -ForegroundColor Green

    Write-Step "Build $DistName wheel"
    & $Python -m build --wheel --outdir $DistDir $RepoRoot
    Assert-True ($LASTEXITCODE -eq 0) "$DistName wheel build"

    $Wheel = Get-ChildItem $DistDir -Filter $WheelGlob | Select-Object -First 1
    Assert-True ($null -ne $Wheel) "$WheelGlob present in dist"
    Write-Host "Wheel: $($Wheel.Name)"

    Write-Step "Distribution name + single-sourced version"
    $Meta = Get-WheelMetadata $Wheel.FullName

    Assert-True ([bool]($Meta -match "(?m)^Name:\s*$([regex]::Escape($DistName))\s*$")) `
        "wheel METADATA declares Name: $DistName"

    Assert-True ([bool]($Meta -match "(?m)^Version:\s*$([regex]::Escape($SrcVersion))\s*$")) `
        "version single-sourced from violet/__init__.py ($SrcVersion)"

    Assert-True (-not (Test-StaticProjectVersion $Pyproject)) `
        "no static [project] version in pyproject.toml (version stays single-sourced)"

    Write-Step "Import package is violet (dist name != import name)"
    # Hatchling no longer ships setuptools-style top_level.txt; verify the
    # package directory is in the wheel instead.
    $Entries = Get-WheelEntries $Wheel.FullName
    Assert-True ([bool]($Entries -contains "violet/__init__.py")) `
        "wheel contains violet/__init__.py (import name unchanged by the dist name)"

    Write-Step "Install wheel into clean venv"
    & $Python -m venv $VenvDir
    $VenvPython = Join-Path $VenvDir "Scripts\python.exe"
    Assert-True (Test-Path $VenvPython) "venv python exists"
    & $VenvPython -m pip install --upgrade pip | Out-Host
    & $VenvPython -m pip install --no-deps $Wheel.FullName | Out-Host
    Assert-True ($LASTEXITCODE -eq 0) "pip install $DistName from local wheel"
    & $VenvPython -c "import violet; print('import OK', violet.__version__)"
    Assert-True ($LASTEXITCODE -eq 0) "import violet after install"

    if (-not $SkipTests) {
        Write-Step "Offline tests (no network)"
        # Allow deps so [dev] pulls pytest. Runtime deps are empty, so this stays lean.
        & $VenvPython -m pip install -e "$RepoRoot[dev]"
        Assert-True ($LASTEXITCODE -eq 0) "editable install $DistName[dev]"
        & $VenvPython -c "import pytest; print('pytest', pytest.__version__)"
        Assert-True ($LASTEXITCODE -eq 0) "pytest available in venv"
        Push-Location $RepoRoot
        try {
            & $VenvPython -m pytest tests -q
            Assert-True ($LASTEXITCODE -eq 0) "pytest tests"
        } finally {
            Pop-Location
        }
    } else {
        Write-Host "Skipping offline tests (-SkipTests)" -ForegroundColor Yellow
    }

    Write-Step "Twine check (metadata lint)"
    & $Python -m twine check $Wheel.FullName
    Assert-True ($LASTEXITCODE -eq 0) "twine check $DistName"

    if (-not $SkipPyPIProbe) {
        Write-Step "PyPI probe (read-only, anonymous)"
        $live = $false
        try {
            $resp = Invoke-WebRequest -Uri "https://pypi.org/pypi/$DistName/json" -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
            $live = ($resp.StatusCode -eq 200)
        } catch {
            $live = $false
        }
        if ($live) {
            Write-Host "  $DistName : PRESENT on PyPI" -ForegroundColor Green
        } else {
            Write-Host "  $DistName : NOT on PyPI (404)" -ForegroundColor Yellow
        }
    }

    Write-Host "`n========================================" -ForegroundColor Green
    Write-Host " PUBLISH CHECK PASSED — ready to publish" -ForegroundColor Green
    Write-Host " (not published; no credentials used)" -ForegroundColor Green
    Write-Host "========================================`n" -ForegroundColor Green

    Write-Host @"
Publish commands (run manually when tokens are available):

  cd $RepoRoot
  Remove-Item -Recurse -Force dist, build, *.egg-info -ErrorAction SilentlyContinue
  python -m pip install --upgrade build twine
  python -m build --wheel
  python -m twine check dist\*.whl

  # Upload — requires TWINE_USERNAME=__token__ and TWINE_PASSWORD=pypi-...
  python -m twine upload dist\$WheelGlob

  # TestPyPI dry run (recommended first):
  python -m twine upload --repository testpypi dist\$WheelGlob

  # Verify from a clean venv:
  python -m venv .verify; .verify\Scripts\pip install $DistName
  .verify\Scripts\python -c "import violet; print(violet.__version__)"

  # Consumers (dist name != import name — you import violet):
  pip install $DistName

Re-run this check anytime:
  pwsh $PSCommandPath
"@
}
finally {
    if ($KeepWorkDir) {
        Write-Host "Work dir kept: $WorkDir" -ForegroundColor Yellow
    } else {
        Remove-Item -Recurse -Force $WorkDir -ErrorAction SilentlyContinue
    }
}
