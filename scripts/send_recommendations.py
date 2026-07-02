#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Send recommendation cards to Slack users from recommendations.jsonl.

Slack 版：用 Bot Token (xoxb-) 直接调 chat.postMessage 发送 Block Kit 消息。
相比飞书省去了换 tenant_access_token 的一步。

卡片对应关系：
  飞书 interactive card  → Slack Block Kit (blocks)
  飞书 lark_md div       → Slack section + mrkdwn
  飞书 button.value(dict)→ Slack button.value(JSON 字符串，点击时原样回传)
"""

from __future__ import annotations

import argparse
import base64
import json
import os
from datetime import datetime
from typing import Any

import requests

SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"

_DATA_DIR = os.environ.get("TECH_DIGEST_DATA_DIR", os.path.expanduser("~/.tech-digest"))
DEFAULT_PUSHED = os.path.join(_DATA_DIR, "logs", "pushed_ids.json")


def _record_pushed_ids(path: str, new_ids: list[str]) -> None:
    """把本次真正推送出去的 item id 追加进全局已推送档案（去重用）。"""
    if not new_ids:
        return
    existing: list[str] = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            existing = data.get("ids", []) if isinstance(data, dict) else list(data)
        except Exception:
            existing = []
    merged = list(dict.fromkeys(existing + new_ids))  # 去重且保序
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"ids": merged, "updated_at": datetime.now().isoformat()}, f, ensure_ascii=False, indent=2)


def _now_str() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


SOURCE_LABELS = {
    "hf": "📚 HuggingFace 论文",
    "gh": "⭐ GitHub Trending",
    "anthropic": "🤖 Anthropic 动态",
}


def _build_blocks(title: str, push_id: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """构建 Slack Block Kit 消息体（对应飞书的 _build_card）。"""
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": title, "emoji": True}}
    ]

    # 按 source 分组，保持顺序
    from collections import OrderedDict
    groups: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for item in items:
        src = item.get("source") or "other"
        groups.setdefault(src, []).append(item)

    for src, src_items in groups.items():
        label = SOURCE_LABELS.get(src, src.upper())
        blocks.append({"type": "divider"})
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*{label}*"}}
        )

        for item in src_items:
            name = item.get("title") or item.get("id") or "未命名"
            desc = item.get("summary") or item.get("description") or ""
            url = item.get("url") or ""
            # 标题留纯文本：跳转交给下面的「查看」按钮，避免重复
            text = f"*{name}*" + (f"\n{desc}" if desc else "")
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text}})

            value_base = {
                "push_id": push_id,
                "item_id": item.get("id"),
                "source": src,
                "url": url,
                "title": name,
            }

            elements: list[dict[str, Any]] = []
            if url:
                # 「查看」按钮：带 url，点击直接跳转（Slack 中带 url 的 button 不触发回调）
                elements.append(
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "查看", "emoji": True},
                        "style": "primary",
                        "url": url,
                        "value": json.dumps({**value_base, "action": "read"}, ensure_ascii=False),
                        "action_id": "card_action_read",
                    }
                )
            elements.append(
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "感兴趣", "emoji": True},
                    "value": json.dumps({**value_base, "action": "like"}, ensure_ascii=False),
                    "action_id": "card_action_like",
                }
            )
            elements.append(
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "不感兴趣", "emoji": True},
                    "value": json.dumps({**value_base, "action": "dislike"}, ensure_ascii=False),
                    "action_id": "card_action_dislike",
                }
            )
            blocks.append({"type": "actions", "elements": elements})

    blocks.append(
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "点击按钮会记录反馈，用于改进订阅推荐。"}],
        }
    )
    return blocks


def _call_llm(prompt: str, endpoint: str, api_key: str | None, model: str | None) -> str:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model or "default",
        "messages": [
            {
                "role": "system",
                "content": "You are a concise Chinese tech writer. Reply with JSON only.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    res = requests.post(endpoint, headers=headers, json=payload, timeout=30)
    res.raise_for_status()
    data = res.json()
    if isinstance(data, dict) and "choices" in data and data["choices"]:
        return data["choices"][0]["message"]["content"]
    return json.dumps(data, ensure_ascii=False)


def _parse_github_repo(item: dict[str, Any]) -> str | None:
    repo = item.get("id") or item.get("title") or ""
    repo = str(repo)
    if "/" in repo:
        return repo.strip()
    url = str(item.get("url") or "")
    if "github.com/" in url:
        return url.split("github.com/")[-1].strip("/")
    return None


def _extract_readme_text(content_b64: str) -> str:
    try:
        raw = base64.b64decode(content_b64).decode("utf-8", errors="ignore")
    except Exception:
        return ""
    lines = [l.strip() for l in raw.splitlines()]
    cleaned = []
    for line in lines:
        if not line or line.startswith("#") or line.startswith("![") or line.startswith("[!"):
            continue
        cleaned.append(line)
        if len(cleaned) >= 3:
            break
    return " ".join(cleaned).strip()


def _fetch_github_desc_readme(repo: str) -> str:
    if not repo:
        return ""
    headers = {"Accept": "application/vnd.github+json"}
    # 公司出口 IP 共享，未认证调用很容易触发 GitHub 60/小时限速。
    # 设了 GITHUB_TOKEN 则带认证（5000/小时）。无 token 静默降级。
    gh_token = os.environ.get("GITHUB_TOKEN", "").strip()
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"
    desc = ""
    try:
        res = requests.get(f"https://api.github.com/repos/{repo}", headers=headers, timeout=15)
        if res.status_code == 200:
            data = res.json() or {}
            desc = data.get("description") or ""
    except Exception:
        desc = ""
    readme_text = ""
    try:
        res = requests.get(f"https://api.github.com/repos/{repo}/readme", headers=headers, timeout=15)
        if res.status_code == 200:
            data = res.json() or {}
            readme_text = _extract_readme_text(data.get("content") or "")
    except Exception:
        readme_text = ""
    if desc and readme_text:
        return f"{desc}\n{readme_text}"
    return desc or readme_text


def _summarize_item(
    item: dict[str, Any],
    endpoint: str,
    api_key: str | None,
    model: str | None,
) -> str:
    _EMPTY_PLACEHOLDERS = {"无描述", "无法生成摘要", "no description", ""}
    source = item.get("source") or ""
    summary = item.get("summary") or item.get("description") or ""
    if summary.strip().lower() in _EMPTY_PLACEHOLDERS:
        summary = ""
    if source == "gh" and not summary:
        repo = _parse_github_repo(item)
        summary = _fetch_github_desc_readme(repo or "")
    if not summary:
        return "暂无简介"
    payload = {
        "title": item.get("title") or "",
        "source": source,
        "summary": summary,
    }
    prompt = {
        "task": "Compress to 1-2 short Chinese sentences. Do not invent facts. "
        "Return JSON: {\"summary\":\"...\"}. Summary must be non-empty.",
        "item": payload,
    }
    raw = _call_llm(json.dumps(prompt, ensure_ascii=False), endpoint, api_key, model)
    try:
        parsed = json.loads(raw)
        summary = parsed.get("summary") if isinstance(parsed, dict) else ""
        return str(summary or "无法生成摘要")
    except Exception:
        return payload.get("summary") or "无法生成摘要"


def _send_blocks(
    blocks: list[dict[str, Any]],
    channel: str,
    title: str,
    token: str,
) -> dict[str, Any]:
    """通过 Slack chat.postMessage 发送。channel 可以是用户 id（U...）或频道 id（C...）。

    向用户 id 发送时，Slack 会自动开一个 bot 与该用户的私聊（DM）。
    """
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
    payload = {
        "channel": channel,
        "blocks": blocks,
        "text": title,  # fallback 文本（通知预览 / 不支持 blocks 的客户端）
        # 关掉自动 URL preview，否则按钮 url 会被 Slack 再渲染一层 link unfurl 卡，重复
        "unfurl_links": False,
        "unfurl_media": False,
    }
    res = requests.post(SLACK_POST_MESSAGE_URL, headers=headers, json=payload, timeout=15)
    if res.status_code >= 400:
        raise RuntimeError(f"send_failed status={res.status_code} body={res.text}")
    data = res.json()
    # Slack 即使 HTTP 200 也可能 ok=false，需要显式检查
    if not data.get("ok"):
        raise RuntimeError(f"send_failed slack_error={data.get('error')} body={data}")
    return data


def _read_jsonl(path: str) -> list[dict[str, Any]]:
    if not os.path.exists(path):
        return []
    items: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except Exception:
                continue
    return items


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="input_path", required=True)
    parser.add_argument("--title", default="技术订阅推荐")
    parser.add_argument("--llm-summary", action="store_true")
    parser.add_argument("--llm-endpoint", default=os.environ.get("GENREC_ENDPOINT", ""))
    parser.add_argument("--llm-api-key", default=os.environ.get("GENREC_API_KEY"))
    parser.add_argument("--llm-model", default=os.environ.get("GENREC_MODEL"))
    parser.add_argument("--bot-token", default=os.environ.get("SLACK_BOT_TOKEN"))
    parser.add_argument("--pushed-ids", default=DEFAULT_PUSHED,
                        help="推送成功后把 item id 记入此档案，供下次抓取去重")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    bot_token = args.bot_token
    if not bot_token:
        raise RuntimeError(
            "Missing Slack credentials. Set SLACK_BOT_TOKEN (xoxb-...) "
            "environment variable (see .env.example)."
        )

    records = _read_jsonl(args.input_path)
    if not records:
        print(json.dumps({"sent": 0, "reason": "no_records"}, ensure_ascii=False))
        return

    if args.llm_summary and not args.llm_endpoint:
        raise RuntimeError("GENREC_ENDPOINT is required for LLM summaries.")

    sent = 0
    skipped = 0
    failed = 0
    pushed_item_ids: list[str] = []   # 本次真正推送成功的 item id
    summary_cache: dict[str, str] = {}
    for rec in records:
        user_id = rec.get("user_id")
        items = rec.get("items") or []
        if not user_id or not items:
            skipped += 1
            continue
        push_id = f"rec-{_now_str()}"
        if args.llm_summary:
            enriched = []
            for item in items:
                key = json.dumps(
                    {
                        "id": item.get("id"),
                        "title": item.get("title"),
                        "summary": item.get("summary") or item.get("description"),
                        "source": item.get("source"),
                    },
                    ensure_ascii=False,
                )
                if key in summary_cache:
                    summary = summary_cache[key]
                else:
                    summary = _summarize_item(item, args.llm_endpoint, args.llm_api_key, args.llm_model)
                    summary_cache[key] = summary
                enriched.append({**item, "summary": summary})
            items = enriched
        blocks = _build_blocks(args.title, push_id, items)
        if args.dry_run:
            print(json.dumps({"user_id": user_id, "blocks": blocks}, ensure_ascii=False))
            sent += 1
            continue
        try:
            _send_blocks(blocks, user_id, args.title, bot_token)
            sent += 1
            # 推送成功 → 记下这次真正发出去的 item id（供下次去重）
            pushed_item_ids.extend(str(it.get("id")) for it in items if it.get("id"))
        except Exception as e:
            failed += 1
            print(json.dumps({"user_id": user_id, "error": str(e)}, ensure_ascii=False))

    # 非 dry-run 时，把本次成功推送的 id 落盘（下次抓取据此判 same）
    if not args.dry_run:
        _record_pushed_ids(args.pushed_ids, pushed_item_ids)

    print(json.dumps({"sent": sent, "failed": failed, "skipped": skipped}, ensure_ascii=False))


if __name__ == "__main__":
    main()
