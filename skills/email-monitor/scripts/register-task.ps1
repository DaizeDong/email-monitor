<#
register-task.ps1 -- install the EmailMonitorTick heartbeat (idempotent).

Pins an ABSOLUTE pythonw.exe + absolute em_tick.py + WorkingDirectory, because schtasks runs with a
minimal PATH and a PATH-resolved python silently half-runs (ARCHITECTURE anti-pattern #12). Runs every
PT5M, infinite, StartWhenAvailable, IgnoreNew, battery on. Re-run to update.

Usage:
  ./register-task.ps1 -Config "C:\Users\<username>\CodesSelf\email-monitor-config\registry.json" `
                      -Pythonw "C:\ProgramData\miniconda3\pythonw.exe" `
                      [-ResolveCred "C:\Users\<username>\CodesSelf\email-monitor-config\scripts\resolve-cred.ps1"] `
                      [-IntervalMinutes 5]
#>
param(
  [Parameter(Mandatory = $true)][string]$Config,
  [string]$Pythonw = "C:\ProgramData\miniconda3\pythonw.exe",
  [string]$ResolveCred = "",
  [int]$IntervalMinutes = 5,
  [string]$TaskName = "EmailMonitorTick"
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$tick = Join-Path $here "em_tick.py"

if (-not (Test-Path $Pythonw)) { throw "pythonw not found: $Pythonw" }
if (-not (Test-Path $tick))    { throw "em_tick.py not found: $tick" }
if (-not (Test-Path $Config))  { throw "config not found: $Config" }

$args = "`"$tick`" --config `"$Config`""
if ($ResolveCred) { $args += " --resolve-cred `"$ResolveCred`"" }

$action  = New-ScheduledTaskAction -Execute $Pythonw -Argument $args -WorkingDirectory $here
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
             -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
             -RepetitionDuration (New-TimeSpan -Days 1)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
             -MultipleInstances IgnoreNew `
             -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
             -ExecutionTimeLimit (New-TimeSpan -Hours 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
  -Settings $settings -Force | Out-Null
Write-Host "Registered $TaskName (every $IntervalMinutes min). pythonw=$Pythonw"
Write-Host "NOTE: re-arm tomorrow's daily-summary event the first time via em_summary.py if not seeded."
