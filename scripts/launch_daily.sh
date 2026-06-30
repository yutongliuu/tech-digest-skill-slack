#!/usr/bin/env bash
# Launcher for daily recommendations — sources .env then runs the pipeline.
# Called by launchd (com.techdigest.daily.plist).
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
set -a && source "${SKILL_DIR}/.env" && set +a
exec bash "${SKILL_DIR}/scripts/run_daily_recommendations.sh"
