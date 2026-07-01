# run_daily.ps1 - Run the full tech-digest pipeline once (Windows)
# Equivalent of macOS/Linux scripts/run_daily_recommendations.sh
#
# Usage (from project root):
#   powershell -ExecutionPolicy Bypass -File windows\run_daily.ps1

$ErrorActionPreference = "Stop"

# Locate project root (this script lives in windows/)
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SkillDir  = Split-Path -Parent $ScriptDir
Set-Location $SkillDir

# Find python
$PyDir = "$env:LOCALAPPDATA\Programs\Python\Python312-arm64"
if (Test-Path "$PyDir\python.exe") {
    $PyExe = "$PyDir\python.exe"
} else {
    $PyExe = "python"
}

# Load .env (KEY=VALUE, ignore comments/empty)
$EnvFile = Join-Path $SkillDir ".env"
if (-not (Test-Path $EnvFile)) {
    Write-Error ".env not found. Run: copy .env.example .env, then fill in credentials."
    exit 1
}
Get-Content $EnvFile -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if ($line -eq "" -or $line.StartsWith("#")) { return }
    $idx = $line.IndexOf("=")
    if ($idx -lt 1) { return }
    $key = $line.Substring(0, $idx).Trim()
    $val = $line.Substring($idx + 1).Trim()
    [System.Environment]::SetEnvironmentVariable($key, $val, "Process")
}

# Corporate CA: tell requests/pip to trust Netskope at runtime
$Cert = Join-Path $SkillDir "netskope.crt"
if (Test-Path $Cert) {
    $env:REQUESTS_CA_BUNDLE = $Cert
    $env:SSL_CERT_FILE      = $Cert
}

# Force UTF-8 stdout (Windows default cp1252 chokes on non-ASCII)
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

# Data dir
if ([string]::IsNullOrEmpty($env:TECH_DIGEST_DATA_DIR)) {
    $DataDir = Join-Path $env:USERPROFILE ".tech-digest"
} else {
    $DataDir = $env:TECH_DIGEST_DATA_DIR
}
$LogDir    = Join-Path $DataDir "logs"
$StateFile = Join-Path $DataDir "state.json"
$Recs      = Join-Path $LogDir "recommendations.jsonl"
$ActionLog = Join-Path $LogDir "slack_card_actions.jsonl"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# Required-field checks
if ([string]::IsNullOrEmpty($env:GENREC_ENDPOINT)) { Write-Error "GENREC_ENDPOINT not set (see .env)"; exit 1 }
if ([string]::IsNullOrEmpty($env:GENREC_API_KEY))  { Write-Error "GENREC_API_KEY not set (see .env)";  exit 1 }
if ([string]::IsNullOrEmpty($env:SLACK_BOT_TOKEN)) { Write-Error "SLACK_BOT_TOKEN not set (see .env)"; exit 1 }
if ([string]::IsNullOrEmpty($env:GENREC_MODEL))    { $env:GENREC_MODEL = "deepseek-chat" }
if ([string]::IsNullOrEmpty($env:HF_ENDPOINT))     { $env:HF_ENDPOINT = "https://huggingface.co" }

# Sources mode: all = three sources; daily = HF + GitHub only
$Mode = if ([string]::IsNullOrEmpty($env:TECH_DIGEST_MODE)) { "all" } else { $env:TECH_DIGEST_MODE }

# ── Auto-start Socket Mode receiver if not already running ──
# This ensures button clicks (like/dislike) are recorded for personalization.
# The receiver stays alive after this script exits (background process).
#
# Two subtleties this handles:
#   1. Get-Process doesn't expose CommandLine for hidden-window processes
#      → use Get-CimInstance Win32_Process instead.
#   2. Concurrent invocations (e.g. Claude triggers while a scheduled task also
#      fires) can both see "no receiver" and each spawn one → duplicates.
#      → use a file lock (opened FileShare.None) to serialize the check+spawn.
$SocketScript = Join-Path $SkillDir "scripts\slack_socket_mode.py"
$LockFile     = Join-Path $LogDir  "slack_socket.lock"

$lock = $null
try {
    # Exclusive lock: if another run_daily is already in this critical section,
    # New-Object throws and we skip our spawn (assuming the other one will spawn).
    $lock = [System.IO.File]::Open($LockFile, 'OpenOrCreate', 'Write', 'None')

    $SocketRunning = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -eq "python.exe" -and $_.CommandLine -match "slack_socket_mode\.py" }
    if (-not $SocketRunning) {
        Write-Output "[pre] Starting Socket Mode receiver (background)..."
        $SocketLog = Join-Path $LogDir "slack_socket.out"
        $SocketErr = Join-Path $LogDir "slack_socket.err"
        # ⚠️ Do NOT use Start-Process with -RedirectStandardOutput/Error, and
        # do not use PowerShell background jobs either — both keep stdio
        # handles / job objects attached to this shell, blocking the caller
        # (e.g. Claude Code) until the child exits. But this child is the
        # long-lived Socket Mode receiver → it never exits → parent hangs.
        #
        # WMI Win32_Process.Create truly detaches: the new process's parent
        # is the WMI service, not this PowerShell.
        #
        # Because WMI spawns processes with a *fresh* environment (no env
        # inheritance from the current shell), we wrap Python in a small
        # PowerShell command that first loads .env — otherwise the receiver
        # won't have SLACK_*_TOKEN and dies immediately.
        $Cert = Join-Path $SkillDir "netskope.crt"
        $bootstrap = @"
Get-Content '$EnvFile' -Encoding UTF8 | ForEach-Object {
  `$line = `$_.Trim()
  if (`$line -eq '' -or `$line.StartsWith('#')) { return }
  `$idx = `$line.IndexOf('=')
  if (`$idx -lt 1) { return }
  [System.Environment]::SetEnvironmentVariable(`$line.Substring(0,`$idx).Trim(), `$line.Substring(`$idx+1).Trim(), 'Process')
}
if (Test-Path '$Cert') { `$env:REQUESTS_CA_BUNDLE = '$Cert'; `$env:SSL_CERT_FILE = '$Cert' }
`$env:PYTHONIOENCODING = 'utf-8'
`$env:PYTHONUTF8 = '1'
& '$PyExe' -u '$SocketScript' *> '$SocketLog' 2> '$SocketErr'
"@
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($bootstrap))
        $childCmd = "powershell.exe -NoProfile -WindowStyle Hidden -EncodedCommand $encoded"
        $result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $childCmd }
        if ($result.ReturnValue -eq 0) {
            Write-Output "      Started (pid: $($result.ProcessId), log: $SocketLog)"
        } else {
            Write-Output "      [WARN] Failed to spawn receiver (WMI ReturnValue=$($result.ReturnValue))"
        }
        # Give the new process a moment to register in the process table so a
        # closely-following concurrent run will see it.
        Start-Sleep -Milliseconds 500
    } else {
        Write-Output "[pre] Socket Mode receiver already running (pid: $($SocketRunning.ProcessId))"
    }
} catch [System.IO.IOException] {
    # Another run_daily has the lock right now → it will handle the check.
    Write-Output "[pre] Another run_daily is managing the receiver; skipping check."
} finally {
    if ($lock) { $lock.Close() }
}
Write-Output ""

Write-Output "=== tech-digest push (Windows) ==="
Write-Output "Python  : $PyExe"
Write-Output "Data dir: $DataDir"
Write-Output "Mode    : $Mode"
Write-Output ""

# Step 1: build recommendations
Write-Output "[1/2] Fetching + reranking..."
& $PyExe "$SkillDir\scripts\genrec_pipeline.py" `
    --mode $Mode `
    --state $StateFile `
    --log $ActionLog `
    --seed-users $env:SLACK_DEFAULT_USERS `
    --out $Recs
if ($LASTEXITCODE -ne 0) { Write-Error "genrec_pipeline.py failed"; exit 1 }

# Step 2: send cards
Write-Output "[2/2] Summarizing + sending to Slack..."
& $PyExe "$SkillDir\scripts\send_recommendations.py" `
    --llm-summary `
    --in $Recs
if ($LASTEXITCODE -ne 0) { Write-Error "send_recommendations.py failed"; exit 1 }

Write-Output ""
Write-Output "done: $Recs"
