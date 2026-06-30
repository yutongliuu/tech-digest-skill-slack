# install.ps1 - Windows one-click setup: deps + .env + daily scheduled task
# Usage (from project root):
#   powershell -ExecutionPolicy Bypass -File windows\install.ps1
#
# Run twice: first run generates .env for you to fill in;
# second run installs deps and registers the scheduled task.

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SkillDir  = Split-Path -Parent $ScriptDir
Set-Location $SkillDir

Write-Output "=== tech-digest Windows installer ==="
Write-Output "Project dir: $SkillDir"
Write-Output ""

# Step 1: Find Python
$PyDir = "$env:LOCALAPPDATA\Programs\Python\Python312-arm64"
$PyExe = if (Test-Path "$PyDir\python.exe") { "$PyDir\python.exe" } else { "python" }
try {
    $pyver = & $PyExe --version 2>&1
    Write-Output "[1/4] Python: $pyver"
} catch {
    Write-Error "Python not found. Install with: winget install Python.Python.3.12"
    exit 1
}

# Step 2: Create .env from template if missing, then stop for user to fill
$EnvFile = Join-Path $SkillDir ".env"
if (-not (Test-Path $EnvFile)) {
    Copy-Item (Join-Path $SkillDir ".env.example") $EnvFile
    Write-Output "[2/4] Created .env from template"
    Write-Output ""
    Write-Output "  Fill in credentials at: $EnvFile"
    Write-Output "  Then re-run this script."
    Write-Output ""
    exit 0
} else {
    Write-Output "[2/4] .env already exists"
}

# Load .env to validate credentials
Get-Content $EnvFile -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if ($line -eq "" -or $line.StartsWith("#")) { return }
    $idx = $line.IndexOf("=")
    if ($idx -lt 1) { return }
    [System.Environment]::SetEnvironmentVariable($line.Substring(0,$idx).Trim(), $line.Substring($idx+1).Trim(), "Process")
}
if ([string]::IsNullOrEmpty($env:SLACK_BOT_TOKEN) -or $env:SLACK_BOT_TOKEN -like "*xxx*") {
    Write-Output ""
    Write-Output "  .env not configured yet (SLACK_BOT_TOKEN still placeholder): $EnvFile"
    Write-Output "  Fill in and re-run this script."
    Write-Output ""
    exit 0
}

# Step 3: Install Python deps (with corporate cert if present)
Write-Output "[3/4] Installing Python dependencies..."
$Cert = Join-Path $SkillDir "netskope.crt"
$pipArgs = @("-m", "pip", "install", "--upgrade", "requests", "beautifulsoup4", "slack_bolt", "slack_sdk")
if (Test-Path $Cert) { $pipArgs = @("-m", "pip", "install", "--upgrade", "--cert", $Cert, "requests", "beautifulsoup4", "slack_bolt", "slack_sdk") }
& $PyExe $pipArgs
if ($LASTEXITCODE -ne 0) { Write-Error "pip install failed"; exit 1 }
Write-Output "      Done"

# Step 4: Register daily scheduled task
# Push time comes from .env (PUSH_TIME=HH:MM), in this machine's local timezone.
$PushTime = if ([string]::IsNullOrEmpty($env:PUSH_TIME)) { "10:00" } else { $env:PUSH_TIME.Trim() }
if ($PushTime -notmatch '^([01]?\d|2[0-3]):[0-5]\d$') {
    Write-Output "  [WARN] PUSH_TIME '$PushTime' is not valid HH:MM; falling back to 10:00"
    $PushTime = "10:00"
}
Write-Output "[4/4] Registering daily scheduled task at $PushTime (this PC's local time)..."

$TaskName = "TechDigest-Daily"
$RunScript = Join-Path $ScriptDir "run_daily.ps1"

# Trigger: daily at the configured local time
$Trigger = New-ScheduledTaskTrigger -Daily -At $PushTime

# Action: launch hidden powershell running run_daily.ps1
$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$RunScript`""

# Settings: catch up if missed + only on network + allow wake
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

# Principal: run as current user when logged in
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

# Remove any existing task with this name, then register
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Trigger $Trigger `
        -Action $Action `
        -Settings $Settings `
        -Principal $Principal `
        -Description "Daily $PushTime push to Slack. Script: $RunScript" -ErrorAction Stop | Out-Null
} catch {
    Write-Output ""
    Write-Output "  [ERROR] Failed to register the scheduled task:"
    Write-Output "    $($_.Exception.Message)"
    Write-Output ""
    Write-Output "  Common cause: company policy blocks task creation, or this shell"
    Write-Output "  is not elevated. Try running PowerShell as Administrator and re-run."
    exit 1
}

# Verify the task actually exists now (registration can silently no-op under some policies)
$check = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $check) {
    Write-Output ""
    Write-Output "  [ERROR] Task '$TaskName' was NOT found after registration."
    Write-Output "  It may have been blocked or removed by system policy."
    Write-Output "  You can still push manually anytime with windows\run_daily.ps1."
    exit 1
}
Write-Output "      Registered & verified: $TaskName (state: $($check.State))"
Write-Output ""
Write-Output "=== Setup complete ==="
Write-Output ""
Write-Output "Run a manual test now:"
Write-Output "  powershell -ExecutionPolicy Bypass -File windows\run_daily.ps1"
Write-Output ""
Write-Output "Check the task anytime:"
Write-Output "  powershell -ExecutionPolicy Bypass -File windows\check.ps1"
Write-Output ""
Write-Output "Manage the task: open Task Scheduler from Start menu, find $TaskName"
