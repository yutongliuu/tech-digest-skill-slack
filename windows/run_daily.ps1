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
$SocketScript = Join-Path $SkillDir "scripts\slack_socket_mode.py"
$SocketRunning = Get-Process python -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match "slack_socket_mode" }
if (-not $SocketRunning) {
    Write-Output "[pre] Starting Socket Mode receiver (background)..."
    $SocketLog = Join-Path $LogDir "slack_socket.out"
    Start-Process -FilePath $PyExe `
        -ArgumentList "-u", $SocketScript `
        -WindowStyle Hidden `
        -RedirectStandardOutput $SocketLog `
        -RedirectStandardError (Join-Path $LogDir "slack_socket.err")
    Write-Output "      Started (log: $SocketLog)"
} else {
    Write-Output "[pre] Socket Mode receiver already running (pid: $($SocketRunning.Id))"
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
