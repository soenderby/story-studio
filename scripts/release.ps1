<#
.SYNOPSIS
  Cut a Story Studio release: preflight, set the version, date the CHANGELOG section, commit
  "Release X.Y.Z", tag vX.Y.Z and (after confirmation) push both, which starts
  .github/workflows/release.yml. Same behaviour as scripts/release.sh. See docs/RELEASING.md.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\release.ps1 -DryRun 0.2.0
.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\release.ps1 0.2.0
#>
param(
    # The new version: X.Y.Z or X.Y.Z-prerelease (e.g. 0.3.0-beta.1).
    [Parameter(Mandatory = $true, Position = 0)][string]$Version,
    # Run every check and show the changes and commands; change nothing.
    [switch]$DryRun,
    # Push without asking.
    [switch]$Yes
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$Version = $Version -replace '^v', ''
$Tag = "v$Version"

# Python 3: py -3, else python.
if (Get-Command py -ErrorAction SilentlyContinue) { $PyExe = 'py'; $PyArgs = @('-3') }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $PyExe = 'python'; $PyArgs = @() }
else { throw 'Python 3 not found (py or python).' }

# Native commands: never let their stderr turn into a PowerShell error (Windows PowerShell 5.1
# does that under ErrorActionPreference Stop); callers check $LASTEXITCODE instead.
function Invoke-Native {
    param([string]$Exe, [string[]]$Arguments)
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Exe @Arguments } finally { $ErrorActionPreference = $old }
}
function Invoke-Tool { param([string[]]$Arguments) Invoke-Native $PyExe ($PyArgs + @('-B', 'scripts\release_tool.py') + $Arguments) }
function Invoke-Git {
    param([string[]]$Arguments)
    Invoke-Native 'git' $Arguments
    if ($LASTEXITCODE -ne 0) { throw "git $($Arguments -join ' ') failed with exit code $LASTEXITCODE" }
}

$script:Failures = 0
function Ok([string]$Text) { Write-Host "  ok    $Text" }
function Fail([string]$Text) { Write-Host "  FAIL  $Text"; $script:Failures++ }

$suffix = if ($DryRun) { ' (dry run: nothing is changed)' } else { '' }
Write-Host "Story Studio release $Version$suffix"
Write-Host ''
Write-Host 'Preflight'

$branch = Invoke-Native 'git' @('symbolic-ref', '--quiet', '--short', 'HEAD')
if ($LASTEXITCODE -ne 0 -or -not $branch) { $branch = '(detached HEAD)' }
if ($branch -eq 'main') { Ok 'on main' } else { Fail "on main (on $branch)" }

$status = @(Invoke-Native 'git' @('status', '--porcelain'))
if ($LASTEXITCODE -eq 0 -and $status.Count -eq 0) { Ok 'working tree clean' }
else { Fail 'working tree clean:'; $status | ForEach-Object { Write-Host "          $_" } }

Invoke-Native 'git' @('fetch', '--quiet', 'origin', 'main')
if ($LASTEXITCODE -eq 0) {
    $head = "$(Invoke-Native 'git' @('rev-parse', 'HEAD'))".Trim()
    $remote = "$(Invoke-Native 'git' @('rev-parse', 'origin/main'))".Trim()
    if ($head -eq $remote) { Ok 'up to date with origin/main' }
    else { Fail "up to date with origin/main (HEAD $($head.Substring(0, 7)), origin/main $($remote.Substring(0, 7)); pull or push first)" }
} else { Fail 'could not fetch origin/main' }

$null = Invoke-Native 'git' @('rev-parse', '-q', '--verify', "refs/tags/$Tag")
if ($LASTEXITCODE -eq 0) { Fail "tag $Tag already exists locally" }
elseif (Invoke-Native 'git' @('ls-remote', '--tags', 'origin', "refs/tags/$Tag")) { Fail "tag $Tag already exists on origin" }
else { Ok "tag $Tag is new" }

$log = [IO.Path]::GetTempFileName()
try {
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $PyExe @PyArgs -B -m unittest discover -s tests *> $log } finally { $ErrorActionPreference = $old }
    $testsOk = $LASTEXITCODE -eq 0
    $lines = @(Get-Content $log)
    if ($testsOk) {
        $ran = ($lines | Where-Object { $_ -match '^Ran \d+ tests?' } | Select-Object -First 1)
        Ok "tests pass ($ran)"
    } else {
        Fail 'tests pass:'
        $lines | Select-Object -Last 30 | ForEach-Object { Write-Host "          $_" }
    }
} finally { Remove-Item $log -ErrorAction SilentlyContinue }

# Version is valid semver and newer; CHANGELOG has entries under "## Unreleased". Prints the diff.
Invoke-Tool @('prepare', $Version, '--dry-run')
if ($LASTEXITCODE -ne 0) { $script:Failures++ }

$originUrl = "$(Invoke-Native 'git' @('remote', 'get-url', 'origin'))".Trim()
$slug = 'soenderby/story-studio'
if ($originUrl -match '^(https://|git@|ssh://git@)github\.com[:/]([^/]+/[^/]+?)(\.git)?$') { $slug = $Matches[2] }
$ActionsUrl = "https://github.com/$slug/actions/workflows/release.yml"
$PushArgs = @('push', '--atomic', 'origin', 'main', $Tag)
$PushCmd = "git $($PushArgs -join ' ')"

Write-Host ''
if ($DryRun) {
    Write-Host 'Would then run:'
    Write-Host '  write src/story_studio/version.py and CHANGELOG.md (diff above)'
    Write-Host "  git commit -m `"Release $Version`" -- src/story_studio/version.py CHANGELOG.md"
    Write-Host "  git tag -a $Tag -m `"Story Studio $Version`""
    Write-Host "  $PushCmd   (after confirmation, unless -Yes)"
    Write-Host "  watch: $ActionsUrl"
    Write-Host ''
    if ($script:Failures -gt 0) { Write-Host "Dry run: $($script:Failures) check(s) failed; a real release would stop here."; exit 1 }
    Write-Host 'Dry run: all checks passed. Nothing was changed.'
    exit 0
}

if ($script:Failures -gt 0) { Write-Host "$($script:Failures) check(s) failed. Nothing was changed."; exit 1 }

Invoke-Tool @('prepare', $Version)
if ($LASTEXITCODE -ne 0) { throw 'release_tool.py prepare failed' }
Invoke-Git @('add', '--', 'src/story_studio/version.py', 'CHANGELOG.md')
Invoke-Git @('commit', '--quiet', '-m', "Release $Version", '--', 'src/story_studio/version.py', 'CHANGELOG.md')
Invoke-Git @('tag', '-a', $Tag, '-m', "Story Studio $Version")
$short = "$(Invoke-Native 'git' @('rev-parse', '--short', 'HEAD'))".Trim()
Write-Host "Committed $short `"Release $Version`" and tagged $Tag."
Write-Host ''

if (-not $Yes) {
    $answer = Read-Host "Push main and $Tag to origin? This starts the release build, which publishes the release. [y/N]"
    if ($answer -notmatch '^(y|yes)$') {
        Write-Host "Not pushed. Push later with:  $PushCmd"
        Write-Host "Or undo with:                 git tag -d $Tag; git reset --hard HEAD~1"
        exit 0
    }
}

Invoke-Git $PushArgs
Write-Host ''
Write-Host "Pushed. Watch the release build: $ActionsUrl"
Write-Host "(or: gh run watch, then pick the newest Release run)"
