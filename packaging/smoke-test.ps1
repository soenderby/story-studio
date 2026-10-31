<#
.SYNOPSIS
  Smoke and regression test of a built installer, entirely in a scratch folder:
  silent install, launch, windowless check, every shutdown path (second-instance handoff,
  POST /api/quit, taskkill, installer /api/quit + /RELAUNCH, Restart Manager fallback,
  uninstaller), clean tray.log and no error dialog after each, uninstall keeps app data.
  Uses its own STORY_STUDIO_HOME, so a real Story Studio (default port 8765) is never touched.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File packaging\smoke-test.ps1
#>
param(
    [string]$Installer = (Get-ChildItem (Join-Path $PSScriptRoot 'Output\StoryStudio-Setup-*.exe') |
                          Sort-Object LastWriteTime | Select-Object -Last 1).FullName,
    [int]$Port = 8793
)
$ErrorActionPreference = 'Stop'
if ($Port -eq 8765) { throw 'Use a test port, not 8765 (a real Story Studio may be running there).' }
if (-not $Installer -or -not (Test-Path $Installer)) { throw 'No installer found; run packaging\build.ps1 first.' }
"Installer: $Installer"
$T = Join-Path $env:TEMP 'story-studio-smoke-test'
if (Test-Path $T) { Remove-Item -Recurse -Force $T }
$Home2 = Join-Path $T 'home'
$Dir = Join-Path $T 'install'
New-Item -ItemType Directory -Force $Home2 | Out-Null
Set-Content -Path (Join-Path $Home2 'settings.json') -Value "{`"version`": 1, `"port`": $Port}" -Encoding ascii
$env:STORY_STUDIO_HOME = $Home2
$Base = "http://127.0.0.1:$Port"
$Exe = Join-Path $Dir 'StoryStudio.exe'
$Inst = Join-Path $Home2 'instance.json'
$TrayLog = Join-Path $Home2 'logs\tray.log'
$script:Failures = 0
trap {
  # Leave nothing behind on an unexpected error: stop the test instance, uninstall.
  Get-Process StoryStudio -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$Dir*" } | Stop-Process -Force
  $u = Join-Path $Dir 'unins000.exe'
  if (Test-Path $u) { (Start-Process $u -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -PassThru).WaitForExit() }
  break
}

function Step($t) { Write-Host "`n=== $t" }
function Check($name, $ok) { if ($ok) { "PASS  $name" } else { "FAIL  $name"; $script:Failures++ } }
function Info { try { (Invoke-WebRequest "$Base/api/info" -UseBasicParsing -TimeoutSec 3).Content -replace '\s+', ' ' } catch { '' } }
function Procs { @(Get-Process StoryStudio -ErrorAction SilentlyContinue) }
function WaitFor([scriptblock]$cond, [int]$sec = 15) { $d = (Get-Date).AddSeconds($sec); while ((Get-Date) -lt $d) { if (& $cond) { return $true }; Start-Sleep -Milliseconds 250 }; return $false }
function Launch {
  $p = Start-Process $Exe -ArgumentList '--no-browser' -PassThru
  $null = $p.Handle  # keep a handle so ExitCode is readable later
  $null = WaitFor { (Info) -like '*story-studio*' }
  $p
}
function Running {  # the instance started by someone else (the installer)
  $null = WaitFor { (Procs).Count -eq 1 -and (Info) -like '*story-studio*' }
  $p = (Procs)[0]; $null = $p.Handle; $p
}
function Token { (Get-Content $Inst -Raw | ConvertFrom-Json).token }
function LogLines { if (Test-Path $TrayLog) { @(Get-Content $TrayLog) } else { @() } }
function ExpectCleanExit($p, $name, $since) {
  $exited = WaitFor { $p.HasExited } 15
  Check "$name : process exited (a crash dialog would keep it alive)" $exited
  if (-not $exited) { "      MainWindowTitle: '$($p.MainWindowTitle)'"; Stop-Process -Id $p.Id -Force; return }
  Check "$name : exit code 0 (got $($p.ExitCode))" ($p.ExitCode -eq 0)
  Check "$name : instance.json removed" (-not (Test-Path $Inst))
  $new = LogLines | Select-Object -Skip $since
  $bad = @($new | Where-Object { $_ -match 'ERROR|CRITICAL|Traceback|Error' })
  Check "$name : tray.log clean ($($new.Count) new lines)" ($bad.Count -eq 0)
  $new | ForEach-Object { "      log: $_" }
}
function Install([string[]]$extra, $log) {
  $p = Start-Process $Installer -ArgumentList (@('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=$T\$log") + $extra) -PassThru
  $p.WaitForExit()  # not Start-Process -Wait: that also waits for the relaunched app
  $p.ExitCode
}

Step 'before'
Check "port $Port free" (-not (Info))
Check 'no StoryStudio running' ((Procs).Count -eq 0)

Step 'silent install to scratch dir'
Check 'installer exit code 0' ((Install @("/DIR=$Dir") 'install1.log') -eq 0)
Check 'exe installed' (Test-Path $Exe)
Check 'start menu shortcut' (Test-Path "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Story Studio.lnk")
Check 'no desktop shortcut by default' (-not (Test-Path ([Environment]::GetFolderPath('Desktop') + '\Story Studio.lnk')))
Check 'not launched without /RELAUNCH' ((Procs).Count -eq 0)

Step 'launch; serves; windowless'
$n = (LogLines).Count
$a = Launch
$info = Info
"      api/info: $info"
Check 'api/info answers story-studio' ($info -like '*"app": "story-studio"*')
$page = Invoke-WebRequest "$Base/" -UseBasicParsing
Check "GET / 200 editor page ($([regex]::Match($page.Content, '<title>(.*?)</title>').Groups[1].Value))" ($page.StatusCode -eq 200 -and $page.Content -match '<html')
$a.Refresh()
Check "no window (MainWindowHandle $($a.MainWindowHandle))" ($a.MainWindowHandle -eq 0)
Check 'no child process (no conhost)' (@(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($a.Id)").Count -eq 0)

Step 'path: second-instance handoff'
$b = Start-Process $Exe -ArgumentList '--no-browser' -PassThru; $null = $b.Handle
Check 'second instance exits' (WaitFor { $b.HasExited } 20)
Check "second instance exit code 0 (got $($b.ExitCode))" ($b.ExitCode -eq 0)
Check 'first instance still running and serving' ((-not $a.HasExited) -and (Info))

Step 'path: POST /api/quit'
$r = Invoke-WebRequest "$Base/api/quit" -Method Post -Headers @{'X-Story-Token' = (Token)} -UseBasicParsing
Check "quit answered $($r.StatusCode)" ($r.StatusCode -eq 200)
ExpectCleanExit $a 'api/quit' $n

Step 'path: taskkill'
$n = (LogLines).Count
$a = Launch
"      " + (taskkill.exe /PID $a.Id 2>&1)
Check 'taskkill without /F: ignored (no visible window to close), still running' (-not (WaitFor { $a.HasExited } 5))
"      " + (taskkill.exe /F /PID $a.Id 2>&1)
Check 'taskkill /F: terminated' (WaitFor { $a.HasExited } 10)
Check 'taskkill /F: stale instance.json left behind (expected)' (Test-Path $Inst)
$bad = @(LogLines | Select-Object -Skip $n | Where-Object { $_ -match 'ERROR|CRITICAL|Traceback' })
Check 'taskkill: tray.log clean' ($bad.Count -eq 0)
$n = (LogLines).Count
$a = Launch
Check 'next launch starts despite the stale instance.json' ((-not $a.HasExited) -and (Info))
Invoke-WebRequest "$Base/api/quit" -Method Post -Headers @{'X-Story-Token' = (Token)} -UseBasicParsing | Out-Null
ExpectCleanExit $a 'after taskkill /F: api/quit' $n

Step 'path: installer over running app, /RELAUNCH (installer POSTs /api/quit)'
$n = (LogLines).Count
$a = Launch
Check 'installer exit code 0' ((Install @('/RELAUNCH') 'install2.log') -eq 0)
ExpectCleanExit $a 'installer /api/quit' $n
Select-String -Path "$T\install2.log" -Pattern 'Story Studio:|RestartManager found' | ForEach-Object { "      setup: $($_.Line)" }
$a = Running
Check "relaunched (pid $($a.Id)), with --no-browser" ((Get-CimInstance Win32_Process -Filter "ProcessId=$($a.Id)").CommandLine -like '*--no-browser*')

Step 'path: installer with a wrong token (Restart Manager fallback)'
$n = (LogLines).Count
(Get-Content $Inst -Raw) -replace '"token": "[^"]*"', '"token": "wrong"' | Set-Content $Inst -Encoding utf8
Check 'installer exit code 0' ((Install @('/RELAUNCH') 'install3.log') -eq 0)
$exited = WaitFor { $a.HasExited } 15
Check 'old instance exited via Restart Manager' $exited
Check "exit code 0 (got $($a.ExitCode))" ($a.ExitCode -eq 0)
$new = LogLines | Select-Object -Skip $n
$bad = @($new | Where-Object { $_ -match 'ERROR|CRITICAL|Traceback' })
Check "tray.log clean (a 403 WARNING for the wrong token is expected)" ($bad.Count -eq 0)
$new | ForEach-Object { "      log: $_" }
Select-String -Path "$T\install3.log" -Pattern 'Story Studio:|RestartManager found|Shutting down' | ForEach-Object { "      setup: $($_.Line)" }
$a = Running
Check "relaunched again (pid $($a.Id))" ($a -and -not $a.HasExited)

Step 'silent uninstall while running'
$n = (LogLines).Count
$p = Start-Process (Join-Path $Dir 'unins000.exe') -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=$T\uninstall.log" -PassThru
$p.WaitForExit()
Check "uninstaller exit code 0 (got $($p.ExitCode))" ($p.ExitCode -eq 0)
ExpectCleanExit $a 'uninstaller /api/quit' $n
Check 'install dir gone' (WaitFor { -not (Test-Path $Dir) } 30)
Check "app data kept ($((Get-ChildItem $Home2 -Name) -join ', '))" (Test-Path (Join-Path $Home2 'settings.json'))
Check 'start menu shortcut gone' (-not (Test-Path "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Story Studio.lnk"))
Check 'uninstall registry key gone' (-not (Test-Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{15941046-8201-491A-A5E0-2A057BDD1F20}_is1'))
Check 'nothing left running' ((Procs).Count -eq 0)

Remove-Item -Recurse -Force $T
Write-Host "`nFailures: $script:Failures"
exit [int]($script:Failures -ne 0)
