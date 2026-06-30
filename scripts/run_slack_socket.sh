#!/usr/bin/env bash
# Start the Slack Socket Mode callback receiver.
# Usage: ./run_slack_socket.sh [data_dir]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DATA_DIR="${1:-${TECH_DIGEST_DATA_DIR:-${HOME}/.tech-digest}}"
LOG_DIR="${DATA_DIR}/logs"
PID_FILE="${LOG_DIR}/slack_socket.pid"
OUT_FILE="${LOG_DIR}/slack_socket.out"

mkdir -p "${LOG_DIR}"

if [[ -f "${PID_FILE}" ]]; then
  PID="$(cat "${PID_FILE}")"
  if kill -0 "${PID}" >/dev/null 2>&1; then
    echo "already running: pid=${PID}"
    exit 0
  fi
  rm -f "${PID_FILE}"
fi

SLACK_CALLBACK_LOG="${LOG_DIR}/slack_card_actions.jsonl" \
nohup python3 "${SCRIPT_DIR}/slack_socket_mode.py" \
  >>"${OUT_FILE}" 2>&1 &
echo $! > "${PID_FILE}"
echo "started: pid=$(cat "${PID_FILE}")"
