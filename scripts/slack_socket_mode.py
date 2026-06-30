#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Slack Socket Mode receiver for interactive card button actions.

Slack 对应飞书的「WebSocket 长连接」概念叫 Socket Mode：bot 主动连出去，
不需要公网 IP / 域名 / HTTPS 证书，内网机器也能收到按钮点击回调。

用户点击卡片上的「感兴趣 / 不感兴趣」按钮 → Slack 通过 Socket Mode 推送
block_actions 事件 → 这里把点击记录追加到 jsonl，作为下次推荐的个性化输入。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

# Always log to stdout at INFO so we can see connect/event activity.
# Force unbuffered so it shows up live when piped to a file.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
    force=True,
)
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

logger = logging.getLogger("tech-digest.socket")

_DATA_DIR = os.environ.get("TECH_DIGEST_DATA_DIR", os.path.expanduser("~/.tech-digest"))
DEFAULT_LOG = os.path.join(_DATA_DIR, "logs", "slack_card_actions.jsonl")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_log(path: str, record: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _safe_load_value(raw: Any) -> dict[str, Any]:
    """Slack button `value` is a string; we stored JSON in it. Parse it back."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"raw": raw}
        except Exception:
            return {"raw": raw}
    return {}


def build_app(log_path: str) -> App:
    app = App(token=os.environ.get("SLACK_BOT_TOKEN"))

    # action_id 以 "card_action_" 开头的按钮统一在这里处理（like / dislike）。
    @app.action(re.compile(r"^card_action_"))
    def handle_card_action(ack, body, action):  # noqa: ANN001
        ack()
        value = _safe_load_value(action.get("value"))
        user = (body.get("user") or {})
        record = {
            "received_at": _now_iso(),
            "event_type": "block_actions",
            "open_id": user.get("id"),          # Slack user id, e.g. U0XXXXX
            "user_id": user.get("username"),
            "open_message_id": (body.get("container") or {}).get("message_ts"),
            "action_tag": action.get("action_id"),
            "action_value": value,
            "payload": body,
        }
        _append_log(log_path, record)
        logger.info(
            "recorded action: user=%s action=%s item=%s",
            user.get("id"),
            value.get("action") if isinstance(value, dict) else "?",
            value.get("item_id") if isinstance(value, dict) else "?",
        )

    # Catch-all so we see when an event arrives but no handler matched
    # (e.g. action_id naming mismatch). Without this, unmatched actions
    # are silently dropped and the user sees a Slack timeout/spinner.
    @app.action(re.compile(r".*"))
    def handle_unmatched(ack, body, action):  # noqa: ANN001
        ack()
        logger.warning(
            "unmatched action: action_id=%s value=%s",
            action.get("action_id"),
            (action.get("value") or "")[:120],
        )

    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bot-token", default=os.environ.get("SLACK_BOT_TOKEN"))
    parser.add_argument("--app-token", default=os.environ.get("SLACK_APP_TOKEN"))
    parser.add_argument("--log", default=os.environ.get("SLACK_CALLBACK_LOG", DEFAULT_LOG))
    args = parser.parse_args()

    if not args.bot_token or not args.app_token:
        raise RuntimeError(
            "Missing Slack credentials. Set SLACK_BOT_TOKEN (xoxb-...) and "
            "SLACK_APP_TOKEN (xapp-...) environment variables (see .env.example)."
        )

    # SocketModeHandler 读 SLACK_BOT_TOKEN，需要先放进环境
    os.environ["SLACK_BOT_TOKEN"] = args.bot_token

    logger.info("starting Socket Mode handler (will block) ...")
    logger.info("log file: %s", args.log)

    app = build_app(args.log)
    handler = SocketModeHandler(app, args.app_token)
    handler.start()  # 阻塞，保持长连接


if __name__ == "__main__":
    main()
