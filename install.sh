#!/usr/bin/env bash
# One-click setup for tech-digest-skill.
# Usage:
#   bash install.sh          # first run: creates dirs, installs deps, copies .env
#   bash install.sh          # re-run after filling .env: starts services automatically
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OS="$(uname -s)"

echo "=== tech-digest-skill installer ==="
echo "Skill dir : ${SKILL_DIR}"
echo ""

# ── Step 1: Install Python dependencies ───────────────────────────────────────
if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 not found. Please install Python 3.9+."
  exit 1
fi
echo "[1/4] Installing Python dependencies..."
python3 -m pip install --quiet --upgrade requests beautifulsoup4 slack_bolt slack_sdk
echo "      Done."

# ── Step 2: Create .env from example if not present ──────────────────────────
if [[ ! -f "${SKILL_DIR}/.env" ]]; then
  cp "${SKILL_DIR}/.env.example" "${SKILL_DIR}/.env"
  echo "[2/4] Created .env from template."
  echo ""
  echo "  ✏️  Please fill in your credentials:"
  echo "      ${SKILL_DIR}/.env"
  echo ""
  echo "  Then re-run: bash install.sh"
  echo ""
  exit 0
else
  echo "[2/4] .env already exists, skipping."
fi

# ── Step 3: Check credentials are filled ─────────────────────────────────────
set -a && source "${SKILL_DIR}/.env" && set +a
DATA_DIR="${TECH_DIGEST_DATA_DIR:-${HOME}/.tech-digest}"

if [[ -z "${SLACK_BOT_TOKEN:-}" ]] || [[ "${SLACK_BOT_TOKEN}" == *"xxx"* ]]; then
  echo ""
  echo "  ✏️  .env is not configured yet. Please fill in:"
  echo "      ${SKILL_DIR}/.env"
  echo ""
  echo "  Then re-run: bash install.sh"
  echo ""
  exit 0
fi

mkdir -p "${DATA_DIR}/logs"
chmod +x "${SKILL_DIR}/scripts/"*.sh
echo "[3/4] Data directory: ${DATA_DIR}"

# Push time from .env (PUSH_TIME=HH:MM), in this machine's local timezone. Default 10:00.
PUSH_TIME="${PUSH_TIME:-10:00}"
if [[ ! "${PUSH_TIME}" =~ ^([01]?[0-9]|2[0-3]):[0-5][0-9]$ ]]; then
  echo "      [WARN] PUSH_TIME '${PUSH_TIME}' invalid (need HH:MM); using 10:00"
  PUSH_TIME="10:00"
fi
PUSH_HOUR="${PUSH_TIME%%:*}"
PUSH_MIN="${PUSH_TIME##*:}"
# Strip leading zero so values like "09" don't become invalid octal later
PUSH_HOUR=$((10#${PUSH_HOUR}))
PUSH_MIN=$((10#${PUSH_MIN}))
echo "      Daily push time: ${PUSH_TIME} (local time)"

# ── Step 4: Setup services ────────────────────────────────────────────────────
echo "[4/4] Setting up services..."

if [[ "${OS}" == "Darwin" ]]; then
  # macOS: launchd
  PLIST_DIR="${HOME}/Library/LaunchAgents"
  mkdir -p "${PLIST_DIR}"

  # socket mode plist
  cat > "${PLIST_DIR}/com.techdigest.socket.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.techdigest.socket</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${SKILL_DIR}/scripts/launch_socket.sh</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${DATA_DIR}/logs/slack_socket.out</string>
    <key>StandardErrorPath</key>
    <string>${DATA_DIR}/logs/slack_socket.err</string>
</dict>
</plist>
PLIST

  # daily plist
  cat > "${PLIST_DIR}/com.techdigest.daily.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.techdigest.daily</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${SKILL_DIR}/scripts/launch_daily.sh</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>${PUSH_HOUR}</integer>
        <key>Minute</key>
        <integer>${PUSH_MIN}</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>${DATA_DIR}/logs/daily.out</string>
    <key>StandardErrorPath</key>
    <string>${DATA_DIR}/logs/daily.err</string>
</dict>
</plist>
PLIST

  # load (unload first if already loaded)
  launchctl unload "${PLIST_DIR}/com.techdigest.socket.plist" 2>/dev/null || true
  launchctl unload "${PLIST_DIR}/com.techdigest.daily.plist"    2>/dev/null || true
  launchctl load   "${PLIST_DIR}/com.techdigest.socket.plist"
  launchctl load   "${PLIST_DIR}/com.techdigest.daily.plist"
  echo "      Slack callback receiver: started (auto-restarts on crash/reboot)"
  echo "      Daily digest: scheduled at 09:00 every day"

elif [[ "${OS}" == "Linux" ]]; then
  # Linux: crontab
  TMPFILE="$(mktemp)"
  crontab -l 2>/dev/null | grep -v "tech-digest-skill" > "${TMPFILE}" || true
  echo "@reboot  cd ${SKILL_DIR} && bash scripts/launch_socket.sh >> ${DATA_DIR}/logs/slack_socket.out 2>&1" >> "${TMPFILE}"
  echo "${PUSH_MIN} ${PUSH_HOUR} * * * cd ${SKILL_DIR} && bash scripts/launch_daily.sh   >> ${DATA_DIR}/logs/daily.out 2>&1"          >> "${TMPFILE}"
  crontab "${TMPFILE}"
  rm "${TMPFILE}"

  # start socket receiver now
  bash "${SKILL_DIR}/scripts/launch_socket.sh" &
  echo "      Slack callback receiver: started"
  echo "      Daily digest: scheduled at ${PUSH_TIME} via crontab"

else
  echo "      Unsupported OS: ${OS}. Please set up scheduling manually."
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "Test a manual run now:"
echo "  bash ${SKILL_DIR}/scripts/run_daily_recommendations.sh"
echo ""
echo "Logs:"
echo "  ${DATA_DIR}/logs/"
