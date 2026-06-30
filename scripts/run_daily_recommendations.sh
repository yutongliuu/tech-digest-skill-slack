#!/usr/bin/env bash
# Run the full tech-digest recommendation pipeline.
# Usage: ./run_daily_recommendations.sh [data_dir]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Data directory: arg → env var → ~/.tech-digest
DATA_DIR="${1:-${TECH_DIGEST_DATA_DIR:-${HOME}/.tech-digest}}"
LOG_DIR="${DATA_DIR}/logs"
STATE_FILE="${DATA_DIR}/state.json"
RECS="${LOG_DIR}/recommendations.jsonl"
ACTION_LOG="${LOG_DIR}/slack_card_actions.jsonl"

mkdir -p "${LOG_DIR}"

# LLM settings (required for rerank + card summaries)
export GENREC_ENDPOINT="${GENREC_ENDPOINT:?GENREC_ENDPOINT is required (e.g. https://api.deepseek.com/v1/chat/completions)}"
export GENREC_API_KEY="${GENREC_API_KEY:?GENREC_API_KEY is required}"
export GENREC_MODEL="${GENREC_MODEL:-deepseek-chat}"

# Optional network settings
export TECH_DIGEST_PROXY="${TECH_DIGEST_PROXY:-}"
export HF_ENDPOINT="${HF_ENDPOINT:-https://huggingface.co}"

# Sources mode:
#   all   = Anthropic + HuggingFace + GitHub  (default)
#   daily = HuggingFace + GitHub only         (skip Anthropic, e.g. when it's unreachable)
MODE="${TECH_DIGEST_MODE:-all}"

python3 "${SCRIPT_DIR}/genrec_pipeline.py" \
  --mode "${MODE}" \
  --state "${STATE_FILE}" \
  --log "${ACTION_LOG}" \
  --seed-users "${SLACK_DEFAULT_USERS:-}" \
  --out "${RECS}"

python3 "${SCRIPT_DIR}/send_recommendations.py" \
  --llm-summary \
  --in "${RECS}"

echo "done: ${RECS}"
