# check.ps1 - Diagnose the tech-digest setup health (Windows)
# Usage:
#   powershell -ExecutionPolicy Bypass -File windows\check.ps1
#
# Verifies: scheduled task exists, last run result, socket receiver running,
# .env present, recent activity. Read-only, makes no changes.

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SkillDir  = Split-Path -Parent $ScriptDir
$DataDir   = if ($env:TECH_DIGEST_DATA_DIR) { $env:TECH_DIGEST_DATA_DIR } else { Join-Path $env:USERPROFILE ".tech-digest" }
$LogDir    = Join-Path $DataDir "logs"

Write-Output "=== tech-digest health check ==="
Write-Output ""

# 1. Scheduled task
$TaskName = "TechDigest-Daily"
$t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($t) {
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Output "[OK]   Scheduled task '$TaskName' exists (state: $($t.State))"
    Write-Output "       Next run : $($info.NextRunTime)"
    Write-Output "       Last run : $($info.LastRunTime)  (result: $($info.LastTaskResult))"
} else {
    Write-Output "[MISS] Scheduled task '$TaskName' NOT found."
    Write-Output "       Re-create it by running: windows\install.ps1"
}
Write-Output ""

# 2. .env
$EnvFile = Join-Path $SkillDir ".env"
if (Test-Path $EnvFile) {
    Write-Output "[OK]   .env present"
} else {
    Write-Output "[MISS] .env not found - run install.ps1 first"
}
Write-Output ""

# 3. Socket receiver process
# Match strictly on python.exe running slack_socket_mode.py — a loose regex
# like "slack_socket_mode" also matches diagnostic commands whose command line
# happens to contain that string.
$socket = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -eq "python.exe" -and $_.CommandLine -match "slack_socket_mode\.py" }
if ($socket) {
    $pids = ($socket | ForEach-Object { $_.ProcessId }) -join ", "
    Write-Output "[OK]   Socket Mode receiver running (pid: $pids)"
} else {
    Write-Output "[INFO] Socket Mode receiver not running."
    Write-Output "       It auto-starts on the next push (run_daily.ps1). Button"
    Write-Output "       feedback is only recorded while it's running."
}
Write-Output ""

# 4. Recent push output
$recs = Join-Path $LogDir "recommendations.jsonl"
if (Test-Path $recs) {
    $age = (Get-Date) - (Get-Item $recs).LastWriteTime
    Write-Output "[OK]   Last recommendations: $([int]$age.TotalHours)h ago ($((Get-Item $recs).LastWriteTime))"
} else {
    Write-Output "[INFO] No recommendations.jsonl yet (no push has run)"
}

# 5. Feedback log
$actions = Join-Path $LogDir "slack_card_actions.jsonl"
if (Test-Path $actions) {
    $n = (Get-Content $actions | Measure-Object -Line).Lines
    Write-Output "[OK]   Feedback records: $n click(s) logged"
} else {
    Write-Output "[INFO] No feedback recorded yet (click a card button to start)"
}

Write-Output ""
Write-Output "=== done ==="
