<#
.SYNOPSIS
  Builds the Story Studio installer from a clean checkout: venv, pinned build tools, icon,
  PyInstaller one-folder app, Inno Setup installer. Prints the installer path and SHA-256.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build.ps1
#>
param(
    # Python 3.12+ used to create .venv-build. Default: $env:STORY_STUDIO_PYTHON, py -3, python.
    [string]$Python = $env:STORY_STUDIO_PYTHON,
    # ISCC.exe. Default: $env:ISCC, PATH, then the usual per-user and per-machine Inno Setup 6 folders.
    [string]$Iscc = $env:ISCC
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe $($Arguments -join ' ') failed with exit code $LASTEXITCODE" }
}

# 1. Build venv with the pinned tools.
$Venv = Join-Path $Root '.venv-build'
$VenvPython = Join-Path $Venv 'Scripts\python.exe'
if (-not (Test-Path $VenvPython)) {
    if ($Python) {
        Invoke-Checked $Python @('-m', 'venv', $Venv)
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        Invoke-Checked 'py' @('-3', '-m', 'venv', $Venv)
    } else {
        Invoke-Checked 'python' @('-m', 'venv', $Venv)
    }
}
Invoke-Checked $VenvPython @('-m', 'pip', 'install', '--disable-pip-version-check', '--quiet', '-r', 'packaging\requirements-build.txt')

# 2. Version, from the single source.
$Version = (& $VenvPython -c "import runpy; print(runpy.run_path('src/story_studio/version.py')['__version__'])").Trim()
if ($LASTEXITCODE -ne 0 -or -not $Version) { throw 'Could not read src/story_studio/version.py' }
if ($Version -notmatch '^(\d+\.\d+\.\d+)') { throw "Version '$Version' does not start with X.Y.Z" }
$NumericVersion = $Matches[1]
Write-Host "Story Studio $Version"

# 3. Icon (committed; regenerating it is deterministic).
Invoke-Checked $VenvPython @('packaging\make_icon.py')

# 4. PyInstaller one-folder build -> dist\StoryStudio.
Invoke-Checked $VenvPython @('-m', 'PyInstaller', '--noconfirm', '--clean', '--log-level', 'WARN',
    '--distpath', 'dist', '--workpath', 'build', 'packaging\story_studio.spec')

# 5. Inno Setup -> packaging\Output\StoryStudio-Setup-<version>.exe.
if (-not $Iscc) {
    $Found = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    $Candidates = @(
        $(if ($Found) { $Found.Source }),
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
    ) | Where-Object { $_ -and (Test-Path $_) }
    $Iscc = $Candidates | Select-Object -First 1
    if (-not $Iscc) {
        throw 'ISCC.exe not found. Install Inno Setup 6: winget install --id JRSoftware.InnoSetup -e --scope user'
    }
}
Invoke-Checked $Iscc @('/Q', "/DAppVersion=$Version", "/DAppNumericVersion=$NumericVersion", 'packaging\installer.iss')

$Installer = Join-Path $Root "packaging\Output\StoryStudio-Setup-$Version.exe"
if (-not (Test-Path $Installer)) { throw "Installer not found: $Installer" }
$Item = Get-Item $Installer
$Hash = (Get-FileHash -Algorithm SHA256 $Installer).Hash.ToLower()
Write-Host ''
Write-Host "Installer: $($Item.FullName)"
Write-Host "Size:      $($Item.Length) bytes ($([math]::Round($Item.Length / 1MB, 1)) MB)"
Write-Host "SHA-256:   $Hash"
