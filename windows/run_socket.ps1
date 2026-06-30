# run_socket.ps1 - Start Slack callback receiver (Socket Mode, foreground)
# Equivalent of macOS/Linux scripts/run_slack_socket.sh
#
# This process must stay running: it records "interested/not interested" clicks.
# Close this window = receiver stops. For always-on, see WINDOWS.md.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File windows\run_socket.ps1

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SkillDir  = Split-Path -Parent $ScriptDir
Set-Location $SkillDir

$PyDir = "$env:LOCALAPPDATA\Programs\Python\Python312-arm64"
$PyExe = if (Test-Path "$PyDir\python.exe") { "$PyDir\python.exe" } else { "python" }

# Load .env
$EnvFile = Join-Path $SkillDir ".env"
if (-not (Test-Path $EnvFile)) { Write-Error ".env not found. Run: copy .env.example .env, then fill in credentials."; exit 1 }
Get-Content $EnvFile -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if ($line -eq "" -or $line.StartsWith("#")) { return }
    $idx = $line.IndexOf("=")
    if ($idx -lt 1) { return }
    [System.Environment]::SetEnvironmentVariable($line.Substring(0,$idx).Trim(), $line.Substring($idx+1).Trim(), "Process")
}

# Corporate CA
$Cert = Join-Path $SkillDir "netskope.crt"
if (Test-Path $Cert) { $env:REQUESTS_CA_BUNDLE = $Cert; $env:SSL_CERT_FILE = $Cert }

# Force UTF-8 stdout
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

if ([string]::IsNullOrEmpty($env:SLACK_BOT_TOKEN)) { Write-Error "SLACK_BOT_TOKEN not set (see .env)"; exit 1 }
if ([string]::IsNullOrEmpty($env:SLACK_APP_TOKEN)) { Write-Error "SLACK_APP_TOKEN not set (see .env)"; exit 1 }

Write-Output "=== Slack callback receiver starting (Ctrl+C to stop) ==="
& $PyExe "$SkillDir\scripts\slack_socket_mode.py"
