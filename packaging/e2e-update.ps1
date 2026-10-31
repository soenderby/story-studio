<#
.SYNOPSIS
  End-to-end test of the in-app updater with real installers, entirely in a scratch folder:
  builds versions -From and -To from a temporary copy of the source that trusts a throwaway
  test key, serves a fake signed release of -To on 127.0.0.1, installs -From to a scratch dir
  with its own STORY_STUDIO_HOME, saves an edit to a test story, then checks that a tampered
  installer is refused and that the real update installs, restarts on the same port as -To and
  leaves the story and app data untouched. Uninstalls and cleans up. Exit code 1 on any failure.

  The real signing key is never used, and the repository's update_key.py is never changed.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File packaging\e2e-update.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File packaging\e2e-update.ps1 -KeepBuild   # reuse the builds next time
#>
param(
    # Python 3.12+ for the build venv and the helpers. Default: $env:STORY_STUDIO_PYTHON, py -3, python.
    [string]$Python = $env:STORY_STUDIO_PYTHON,
    [int]$Port = 8796,
    # The fake release server's port. Default: a free port chosen by the OS.
    [int]$ReleasePort = 0,
    [string]$From = '0.2.0',
    [string]$To = '0.2.1',
    # Keep the temporary source copy, its build venv, both installers and the test key for the next run.
    [switch]$KeepBuild
)
$ErrorActionPreference = 'Stop'
if ($Port -eq 8765 -or $ReleasePort -eq 8765) { throw 'Use test ports, not 8765 (a real Story Studio may be running there).' }
$Root = Split-Path -Parent $PSScriptRoot
$Helper = Join-Path $PSScriptRoot 'e2e_update_helper.py'
$Work = Join-Path $env:TEMP 'story-studio-e2e-update'
$Copy = Join-Path $Work 'source'
$Seed = Join-Path $Work 'test-key.seed'
$Home2 = Join-Path $Work 'home'
$Dir = Join-Path $Work 'install'
$Rel = Join-Path $Work 'release'
$StoryDir = Join-Path $Work 'stories\story-one'
$Base = "http://127.0.0.1:$Port"
$Exe = Join-Path $Dir 'StoryStudio.exe'
$Updates = Join-Path $Home2 'updates'
$UninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{15941046-8201-491A-A5E0-2A057BDD1F20}_is1'
$script:Failures = 0
$script:ReleaseServer = $null

if (-not $Python) {
    $Python = if (Get-Command py -ErrorAction SilentlyContinue) { (& py -3 -c 'import sys; print(sys.executable)').Trim() } else { 'python' }
}

function Step($t) { Write-Host "`n=== $t" }
function Check($name, $ok) { if ($ok) { "PASS  $name" } else { "FAIL  $name"; $script:Failures++ } }
function WaitFor([scriptblock]$cond, [int]$sec = 15) { $d = (Get-Date).AddSeconds($sec); while ((Get-Date) -lt $d) { if (& $cond) { return $true }; Start-Sleep -Milliseconds 250 }; return $false }
function Info { try { Invoke-RestMethod "$Base/api/info" -TimeoutSec 3 } catch { $null } }
function Api($path) { Invoke-RestMethod "$Base$path" -TimeoutSec 10 }
function Token { (Invoke-RestMethod "$Base/api/stories" -TimeoutSec 10).token }
function Post($path) { Invoke-RestMethod "$Base$path" -Method Post -Body '{}' -ContentType 'application/json' -Headers @{'X-Story-Token' = (Token)} -TimeoutSec 60 }
function Procs { @(Get-Process StoryStudio -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$Dir\*" }) }
function SetupProcs { @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -like 'StoryStudio-Setup*' }) }
function Hash($file) { if (Test-Path $file) { (Get-FileHash -Algorithm SHA256 $file).Hash } else { 'missing' } }
function Invoke-Checked([string]$Exe2, [string[]]$Arguments) {
    & $Exe2 @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe2 $($Arguments -join ' ') failed with exit code $LASTEXITCODE" }
}
function Set-Version($version) {
    $file = Join-Path $Copy 'src\story_studio\version.py'
    $text = [IO.File]::ReadAllText($file) -replace "__version__ = '[^']*'", "__version__ = '$version'"
    [IO.File]::WriteAllText($file, $text)
}
function Installer($version) { Join-Path $Copy "packaging\Output\StoryStudio-Setup-$version.exe" }
function Cleanup {
    Procs | Stop-Process -Force
    $uninstaller = Join-Path $Dir 'unins000.exe'
    if (Test-Path $uninstaller) { (Start-Process $uninstaller -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -PassThru).WaitForExit() }
    if ($script:ReleaseServer -and -not $script:ReleaseServer.HasExited) { Stop-Process -Id $script:ReleaseServer.Id -Force }
    Remove-Item Env:STORY_STUDIO_HOME, Env:STORY_STUDIO_UPDATE_URL -ErrorAction SilentlyContinue
    $null = WaitFor { -not (Test-Path $Dir) } 30
    if ($KeepBuild) {
        Get-ChildItem $Work | Where-Object { $_.FullName -ne $Copy -and $_.FullName -ne $Seed } | Remove-Item -Recurse -Force
    } elseif (Test-Path $Work) {
        Remove-Item -Recurse -Force $Work
    }
}
trap { Write-Host "ERROR: $_"; Cleanup; exit 1 }

Step 'before'
if (Test-Path $UninstallKey) { throw 'Story Studio is installed for this user; this test would take over its uninstall entry. Uninstall it first.' }
Check "port $Port free" (-not (Info))
if (-not $ReleasePort) {
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $listener.Start(); $ReleasePort = $listener.LocalEndpoint.Port; $listener.Stop()
}
Check "release port $ReleasePort free" (-not (Get-NetTCPConnection -LocalPort $ReleasePort -ErrorAction SilentlyContinue))
Check 'no Story Studio from the scratch dir running' ((Procs).Count -eq 0)
"Python: $Python"

Step "build $From and $To from a temporary copy that trusts a throwaway test key"
$reuse = $KeepBuild -and (Test-Path $Seed) -and (Test-Path (Installer $From)) -and (Test-Path (Installer $To))
if ($reuse) {
    'Reusing the builds and test key from the last -KeepBuild run'
} else {
    if (Test-Path $Work) { Remove-Item -Recurse -Force $Work }
    New-Item -ItemType Directory -Force $Copy | Out-Null
    Copy-Item (Join-Path $Root 'src') (Join-Path $Copy 'src') -Recurse
    Copy-Item (Join-Path $Root 'packaging') (Join-Path $Copy 'packaging') -Recurse
    Get-ChildItem $Copy -Recurse -Directory -Force | Where-Object { $_.Name -in '__pycache__', 'Output' } | Remove-Item -Recurse -Force
    $KeyFile = Join-Path $Copy 'src\story_studio\update_key.py'
    if (-not $KeyFile.StartsWith($Work)) { throw 'Refusing to change update_key.py outside the work folder' }
    $public = Invoke-Checked $Python @($Helper, 'keygen', $Seed, $KeyFile)
    "Test public key: $public"
    foreach ($version in $From, $To) {
        Set-Version $version
        Invoke-Checked 'powershell.exe' @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $Copy 'packaging\build.ps1'), '-Python', $Python)
    }
}
Check "installer $From built" (Test-Path (Installer $From))
Check "installer $To built" (Test-Path (Installer $To))

Step "serve a fake signed release of $To on port $ReleasePort"
$manifest = Invoke-Checked $Python @($Helper, 'publish', $Seed, (Installer $To), $Rel, $To)
"      latest.json: $manifest"
$script:ReleaseServer = Start-Process $Python -ArgumentList '-m', 'http.server', "$ReleasePort", '--bind', '127.0.0.1', '--directory', "`"$Rel`"" -WindowStyle Hidden -PassThru -RedirectStandardError (Join-Path $Work 'release-server.log')
if (-not (WaitFor { try { (Invoke-WebRequest "http://127.0.0.1:$ReleasePort/latest.json" -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200 } catch { $false } })) {
    Get-Content (Join-Path $Work 'release-server.log') -ErrorAction SilentlyContinue | Select-Object -Last 3
    throw "The fake release server did not answer on port $ReleasePort"
}
'PASS  release server answers'

Step "install $From silently to a scratch dir"
New-Item -ItemType Directory -Force $Home2 | Out-Null
Set-Content -Path (Join-Path $Home2 'settings.json') -Value "{`"version`": 1, `"port`": $Port, `"checkUpdates`": true}" -Encoding ascii
$env:STORY_STUDIO_HOME = $Home2
$env:STORY_STUDIO_UPDATE_URL = "http://127.0.0.1:$ReleasePort/"
$p = Start-Process (Installer $From) -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/DIR=`"$Dir`"", "/LOG=`"$Work\install-$From.log`"" -PassThru
$p.WaitForExit()
Check "installer exit code 0 (got $($p.ExitCode))" ($p.ExitCode -eq 0)
Check 'exe installed' (Test-Path $Exe)

Step "launch $From"
$app = Start-Process $Exe -ArgumentList '--no-browser' -PassThru
$null = $app.Handle  # keep a handle so ExitCode is readable later
Check "serves appVersion $From on port $Port" (WaitFor { (Info).appVersion -eq $From } 20)

Step 'automatic check (about 10 s after start)'
Check 'checked by itself' (WaitFor { (Api '/api/update').lastCheck } 40)
$state = Api '/api/update'
"      $($state | ConvertTo-Json -Compress)"
Check "found $To" ($state.available -and $state.latest -eq $To -and $state.state -eq 'idle')

Step 'register a test story with generated media and save an edit'
$sid = (Invoke-Checked $Python @($Helper, 'story', $Port, $StoryDir)).Trim()
"      story id: $sid"
$story = (Api '/api/stories').stories | Where-Object { $_.id -eq $sid }
Check "registered, not missing, 2 media, cover $($story.cover)" ($story -and -not $story.missing -and $story.media -eq 2 -and $story.cover -eq 'scene-one.png')
Check 'saved (story-project.json.bak written)' (Test-Path "$StoryDir\story-project.json.bak")

Step 'manual check'
$state = Post '/api/update/check'
Check "POST /api/update/check: $To available" ($state.available -and $state.latest -eq $To -and $state.current -eq $From)

Step 'a tampered installer is refused'
$good = Join-Path $Rel "StoryStudio-Setup-$To.exe"
Copy-Item $good "$Work\untampered.exe"
$bytes = [IO.File]::ReadAllBytes($good)
$bytes[$bytes.Length - 1000] = $bytes[$bytes.Length - 1000] -bxor 0xFF
[IO.File]::WriteAllBytes($good, $bytes)
$state = Post '/api/update/install'
Check "install started ($($state.state))" ($state.state -in 'downloading', 'verifying', 'error')
Check 'install failed' (WaitFor { (Api '/api/update').state -eq 'error' } 60)
$state = Api '/api/update'
"      error: $($state.error)"
Check 'refused for the checksum' ($state.error -like '*checksum*')
Check 'still offered (Try again)' ($state.available)
Check 'no installer started' ((SetupProcs).Count -eq 0 -and -not (Test-Path (Join-Path $Updates "install-$To.log")))
Check "nothing kept in updates\ ($((Get-ChildItem $Updates -Name -ErrorAction SilentlyContinue) -join ', '))" (@(Get-ChildItem $Updates -ErrorAction SilentlyContinue).Count -eq 0)
Check "still running $From" ((Info).appVersion -eq $From -and -not $app.HasExited)
Copy-Item "$Work\untampered.exe" $good -Force

Step 'snapshot app data and story'
$before = @{
    project  = Hash "$StoryDir\story-project.json"
    backup   = Hash "$StoryDir\story-project.json.bak"
    stories  = Hash "$Home2\stories.json"
}
$settingsBefore = Get-Content "$Home2\settings.json" -Raw | ConvertFrom-Json
$before.GetEnumerator() | ForEach-Object { "      $($_.Key): $($_.Value)" }

Step "install $To from the app"
$state = Post '/api/update/install'
Check "install answered $($state.state)" ($state.state -eq 'downloading')
$seen = New-Object System.Collections.Generic.List[string]
$deadline = (Get-Date).AddSeconds(180)
$back = $false
while ((Get-Date) -lt $deadline) {
    try { $s = Invoke-RestMethod "$Base/api/update" -TimeoutSec 2; if ($seen.Count -eq 0 -or $seen[$seen.Count - 1] -ne $s.state) { $seen.Add($s.state) } } catch { }
    if ((Info).appVersion -eq $To) { $back = $true; break }
    Start-Sleep -Milliseconds 200
}
"      states seen: $($seen -join ' -> ')"
Check "came back by itself as $To on port $Port" $back
Check 'no error on the way' (-not ($seen -contains 'error'))
Check "old process exited, code 0 (got $($app.ExitCode))" ($app.HasExited -and $app.ExitCode -eq 0)
$after = @{
    project  = Hash "$StoryDir\story-project.json"
    backup   = Hash "$StoryDir\story-project.json.bak"
    stories  = Hash "$Home2\stories.json"
}
foreach ($key in 'project', 'backup', 'stories') { Check "$key byte-identical" ($before[$key] -eq $after[$key]) }
$new = (Procs)
Check "one new process (pid $($new.Id), was $($app.Id)), started with --no-browser" ($new.Count -eq 1 -and $new[0].Id -ne $app.Id -and (Get-CimInstance Win32_Process -Filter "ProcessId=$($new[0].Id)").CommandLine -like '*--no-browser*')
$instance = Get-Content "$Home2\instance.json" -Raw | ConvertFrom-Json
Check "instance.json: port $($instance.port), appVersion $($instance.appVersion)" ($instance.port -eq $Port -and $instance.appVersion -eq $To)
$settings = Get-Content "$Home2\settings.json" -Raw | ConvertFrom-Json
Check 'settings.json intact (port, checkUpdates, skippedVersion, lastUpdateCheck)' ($settings.version -eq 1 -and $settings.port -eq $Port -and $settings.checkUpdates -eq $true -and $null -eq $settings.skippedVersion -and $settings.lastUpdateCheck -eq $settingsBefore.lastUpdateCheck)
$story = (Api '/api/stories').stories | Where-Object { $_.id -eq $sid }
Check "story still registered: '$($story.title)', $($story.chapters) chapter(s), $($story.media) media" ($story -and -not $story.missing -and $story.title -eq 'Alpha' -and $story.media -eq 2)
$boot = Api "/s/$sid/api/bootstrap"
Check 'the saved edit is there' ((($boot.project.chapters[0].blocks | Where-Object { $_.id -eq 'e2e-text' }).text) -eq 'Alpha walked through the gate.' -and $boot.appVersion -eq $To)
$state = Api '/api/update'
Check "update state: current $($state.current), state $($state.state)" ($state.current -eq $To -and $state.state -eq 'idle' -and -not $state.available)
$log = Join-Path $Updates "install-$To.log"
Check 'install log written' (Test-Path $log)
if (Test-Path $log) { Select-String -Path $log -Pattern 'Story Studio:' | ForEach-Object { "      setup: $($_.Line)" } }
Check 'installer closed the app through /api/quit' ((Test-Path $log) -and (Select-String -Path $log -Pattern '/api/quit answered 200' -Quiet))
Check 'updates\ cleaned up after the restart: only the install log is left' (WaitFor { $names = @(Get-ChildItem $Updates -Name); $names.Count -eq 1 -and $names[0] -eq "install-$To.log" } 40)
"      updates\: $((Get-ChildItem $Updates -Name) -join ', ')"
$bad = @(Get-ChildItem (Join-Path $Home2 'logs') -Filter *.log | Get-Content | Where-Object { $_ -match 'ERROR|CRITICAL|Traceback' })
Check "logs clean ($($bad.Count) error lines)" ($bad.Count -eq 0)
$bad | ForEach-Object { "      log: $_" }

Step 'quit, uninstall, clean up'
$r = Invoke-WebRequest "$Base/api/quit" -Method Post -Headers @{'X-Story-Token' = $instance.token} -UseBasicParsing
Check "quit answered $($r.StatusCode)" ($r.StatusCode -eq 200)
Check 'app exited' (WaitFor { (Procs).Count -eq 0 } 15)
$p = Start-Process (Join-Path $Dir 'unins000.exe') -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -PassThru
$p.WaitForExit()
Check "uninstaller exit code 0 (got $($p.ExitCode))" ($p.ExitCode -eq 0)
Check 'install dir gone' (WaitFor { -not (Test-Path $Dir) } 30)
Check 'uninstall registry key gone' (-not (Test-Path $UninstallKey))
Check 'story folder kept' (Test-Path "$StoryDir\story-project.json")
Cleanup
Check 'release server stopped' ($script:ReleaseServer.HasExited)
Check 'nothing left running' ((Procs).Count -eq 0 -and (SetupProcs).Count -eq 0)

Write-Host "`nFailures: $script:Failures"
exit [int]($script:Failures -ne 0)
