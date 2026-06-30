#!/usr/bin/env bash
# Launcher for slack_socket_mode — sources .env then starts the process.
# Called by launchd (com.techdigest.socket.plist).
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
set -a && source "${SKILL_DIR}/.env" && set +a
exec python3 "${SKILL_DIR}/scripts/slack_socket_mode.py"
