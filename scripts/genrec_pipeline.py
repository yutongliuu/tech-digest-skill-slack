#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Generate recommendations: rule recall + LLM rerank using interaction history."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from tech_digest import build_report

_DATA_DIR = os.environ.get("TECH_DIGEST_DATA_DIR", os.path.expanduser("~/.tech-digest"))
DEFAULT_LOG = os.path.join(_DATA_DIR, "logs", "slack_card_actions.jsonl")
DEFAULT_OUT = os.path.join(_DATA_DIR, "logs", "recommendations.jsonl")
DEFAULT_PUSHED = os.path.join(_DATA_DIR, "logs", "pushed_ids.json")

TOPIC_KEYWORDS = {
    # 大语言模型 / 生成式 / Agent
    "LLM": [
        r"\bllm\b", r"\bllms\b", r"language model", r"large language",
        r"transformer", r"chatbot", r"\bagent", r"\bgpt\b", r"gpt-\d",
        r"foundation model", r"\bnlp\b", r"instruction", r"fine-tun",
        r"\bprompt", r"in-context", r"\brlhf\b", r"\brag\b", r"retrieval-augmented",
        r"reasoning", r"chain-of-thought", r"\bcot\b", r"mixture-of-experts",
        r"\bmoe\b", r"multimodal", r"\bmllm\b", r"embedding", r"tokeniz",
    ],
    # 计算机视觉 / 图像 / 视频 / 生成
    "CV": [
        r"\bvision\b", r"visual", r"image", r"\bvideo\b", r"diffusion",
        r"segmentation", r"detection", r"\bocr\b", r"generation",
        r"text-to-image", r"text-to-video", r"\bgan\b", r"\bvae\b",
        r"rendering", r"3d", r"nerf", r"gaussian splatting", r"pose",
        r"recognition", r"\bvlm\b", r"vision-language",
    ],
    # 机器人 / 具身智能 / 控制
    "Robotics": [
        r"robot", r"manipulation", r"motion", r"grasp", r"embodied",
        r"locomotion", r"navigation", r"control policy", r"\brl\b",
        r"reinforcement learning", r"imitation learning", r"autonomous",
        r"self-driving", r"driving", r"\bslam\b", r"actuat",
    ],
    # 安全 / 隐私 / 攻击
    "Security": [
        r"security", r"privacy", r"attack", r"adversarial", r"jailbreak",
        r"vulnerab", r"exploit", r"malware", r"encrypt", r"backdoor",
        r"poison", r"watermark", r"red team", r"safety", r"alignment",
    ],
    # 数据 / 评测 / 基准
    "Data": [
        r"dataset", r"benchmark", r"evaluation", r"corpus", r"\beval\b",
        r"annotation", r"data curation", r"synthetic data", r"\bqa\b",
        r"leaderboard", r"metric",
    ],
    # 系统 / 基础设施 / 推理部署
    "Infra": [
        r"system", r"infrastructure", r"deployment", r"serving", r"runtime",
        r"inference", r"training", r"distributed", r"parallel", r"\bgpu\b",
        r"\bcuda\b", r"kernel", r"quantiz", r"compress", r"latency",
        r"throughput", r"optimiz", r"scal", r"framework", r"pipeline",
        r"\bmlops\b", r"kubernetes", r"cluster",
    ],
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _decay(ts: datetime, now: datetime) -> float:
    days = (now - ts).days
    if days <= 7:
        return 1.0
    if days <= 30:
        return 0.5
    return 0.2


def _action_weight(action: str | None) -> float:
    if action == "read":
        return 1.0
    if action == "like":
        return 3.0
    if action == "dislike":
        return -3.0
    return 0.0


def _normalize_text(text: str | None) -> str:
    if not text:
        return ""
    return " ".join(text.lower().split())


def _topic_tags(text: str) -> list[str]:
    tags: list[str] = []
    norm = _normalize_text(text)
    for topic, patterns in TOPIC_KEYWORDS.items():
        for pat in patterns:
            if re.search(pat, norm):
                tags.append(topic)
                break
    if not tags:
        tags.append("Other")
    return tags


def _get_user_id(record: dict[str, Any]) -> str:
    if record.get("open_id"):
        return str(record["open_id"])
    if record.get("user_id"):
        return str(record["user_id"])
    payload = record.get("payload") or {}
    event = payload.get("event") or {}
    operator = event.get("operator") or {}
    return str(operator.get("open_id") or operator.get("user_id") or "unknown")


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


def _extract_history(log_path: str, recent_days: int) -> dict[str, dict[str, list[dict[str, Any]]]]:
    now = _now_utc()
    cutoff = now - timedelta(days=recent_days)
    records = _read_jsonl(log_path)
    per_user_recent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    per_user_older: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for rec in records:
        ts = _parse_time(rec.get("received_at"))
        if not ts:
            continue
        user_id = _get_user_id(rec)
        action_value = rec.get("action_value") or {}
        action = action_value.get("action")
        title = action_value.get("title") or ""
        source = action_value.get("source") or "unknown"
        url = action_value.get("url") or ""
        item = {
            "ts": ts.isoformat(),
            "action": action,
            "title": title,
            "source": source,
            "url": url,
        }
        if ts >= cutoff:
            per_user_recent[user_id].append(item)
        else:
            per_user_older[user_id].append(item)

    def sort_desc(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items.sort(key=lambda x: x.get("ts", ""), reverse=True)
        return items

    def dedupe_recent(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[tuple[str, str, str, str]] = set()
        deduped: list[dict[str, Any]] = []
        for item in items:
            key = (
                str(item.get("action") or ""),
                str(item.get("title") or ""),
                str(item.get("source") or ""),
                str(item.get("url") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    result: dict[str, dict[str, list[dict[str, Any]]]] = {}
    all_users = set(per_user_recent) | set(per_user_older)
    for user_id in all_users:
        recent = sort_desc(per_user_recent.get(user_id, []))
        recent = dedupe_recent(recent)
        older = sort_desc(per_user_older.get(user_id, []))
        result[user_id] = {"recent": recent, "older": older}
    return result


def _build_topic_scores(recent_history: list[dict[str, Any]], now: datetime) -> dict[str, float]:
    scores: dict[str, float] = defaultdict(float)
    for item in recent_history:
        action = item.get("action")
        weight = _action_weight(action)
        if weight == 0.0:
            continue
        ts = _parse_time(item.get("ts"))
        if not ts:
            continue
        score = weight * _decay(ts, now)
        for tag in _topic_tags(item.get("title", "")):
            scores[f"topic:{tag}"] += score
    return scores


def _build_candidates(report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    candidates: dict[str, list[dict[str, Any]]] = {"anthropic": [], "hf": [], "gh": []}

    def add_items(section: str, source: str, title_key: str, summary_key: str) -> None:
        items = (report.get(section) or {}).get("items") or []
        for it in items:
            if it.get("status") == "same":
                continue
            title = it.get(title_key) or it.get("title") or ""
            summary = it.get(summary_key) or it.get("summary") or it.get("description") or ""
            item_id = it.get("id") or it.get("url") or title
            candidates[source].append(
                {
                    "id": str(item_id),
                    "title": str(title),
                    "summary": str(summary),
                    "url": it.get("url") or "",
                    "source": source,
                }
            )

    anthropic = report.get("anthropic") or {}
    if anthropic.get("has_update"):
        add_items("anthropic", "anthropic", "title", "summary")
    add_items("hf", "hf", "title", "summary")
    add_items("gh", "gh", "repository", "description")
    return candidates


def _score_candidate(topic_scores: dict[str, float], cand: dict[str, Any]) -> float:
    text = f"{cand.get('title','')} {cand.get('summary','')}"
    tag_scores = [float(topic_scores.get(f"topic:{tag}", 0.0)) for tag in _topic_tags(text)]
    return max(tag_scores) if tag_scores else 0.0


def _select_by_rule(topic_scores: dict[str, float], candidates: list[dict[str, Any]], k: int) -> list[dict[str, Any]]:
    scored = []
    for cand in candidates:
        score = _score_candidate(topic_scores, cand)
        scored.append({**cand, "score": score})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:k]


def _call_llm(prompt: str, endpoint: str, api_key: str | None, model: str | None) -> str:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model or "default",
        "messages": [
            {"role": "system", "content": "You are a recommendation engine. Reply with JSON only."},
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


def _summarize_history(
    older_history: list[dict[str, Any]],
    endpoint: str,
    api_key: str | None,
    model: str | None,
) -> str:
    if not older_history:
        return ""
    prompt = {
        "task": "Summarize long-term user preferences based on interaction history. "
        "Return JSON: {\"summary\":\"...\"}. Keep 2-3 Chinese sentences.",
        "history": older_history,
    }
    raw = _call_llm(json.dumps(prompt, ensure_ascii=False), endpoint, api_key, model)
    try:
        parsed = json.loads(raw)
        summary = parsed.get("summary") if isinstance(parsed, dict) else ""
        return str(summary or "")
    except Exception:
        return ""


def _rerank_by_llm(
    recent_history: list[dict[str, Any]],
    older_summary: str,
    candidates: list[dict[str, Any]],
    top_k: int,
    endpoint: str,
    api_key: str | None,
    model: str | None,
) -> list[dict[str, Any]]:
    prompt = {
        "task": f"Rerank candidates and return top {top_k} ids. "
        "Only use ids from candidates. Return JSON: {\"ids\":[...]}",
        "recent_history": recent_history,
        "older_summary": older_summary,
        "candidates": [
            {
                "id": c["id"],
                "title": c["title"],
                "summary": c.get("summary", ""),
                "source": c["source"],
            }
            for c in candidates
        ],
    }
    raw = _call_llm(json.dumps(prompt, ensure_ascii=False), endpoint, api_key, model)
    try:
        parsed = json.loads(raw)
        ids = parsed.get("ids") if isinstance(parsed, dict) else None
        if not isinstance(ids, list):
            raise ValueError("invalid ids")
        id_set = [str(i) for i in ids]
        id_map = {str(c["id"]): c for c in candidates}
        ranked = [id_map[i] for i in id_set if i in id_map]
        if not ranked:
            raise ValueError("empty selection")
        return ranked[:top_k]
    except Exception:
        return []


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["anthropic", "daily", "all"], default="all")
    parser.add_argument("--state", required=True)
    parser.add_argument("--report", default=None)
    parser.add_argument("--log", default=DEFAULT_LOG)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--pushed-ids", default=DEFAULT_PUSHED,
                        help="已推送 item id 档案；抓取据此判重（只跳过真正推送过的）")
    parser.add_argument("--recent-days", type=int, default=15)
    parser.add_argument("--hf-top-k", type=int, default=3)
    parser.add_argument("--gh-top-k", type=int, default=3)
    parser.add_argument("--anthropic-top-k", type=int, default=-1)
    parser.add_argument("--hf-candidate-k", type=int, default=10)
    parser.add_argument("--gh-candidate-k", type=int, default=10)
    parser.add_argument("--anthropic-candidate-k", type=int, default=10)
    parser.add_argument("--proxy", default=os.environ.get("TECH_DIGEST_PROXY"))
    parser.add_argument("--llm-endpoint", default=os.environ.get("GENREC_ENDPOINT", ""))
    parser.add_argument("--llm-api-key", default=os.environ.get("GENREC_API_KEY"))
    parser.add_argument("--llm-model", default=os.environ.get("GENREC_MODEL"))
    parser.add_argument(
        "--seed-users",
        default=os.environ.get("SLACK_DEFAULT_USERS", ""),
        help="种子用户：逗号分隔的 Slack 用户/频道 id（如 U07ABC,U07DEF）。"
        "没有点击历史的种子用户会收到非个性化（冷启动）推荐，用于发出第一条推送。",
    )
    args = parser.parse_args()

    if not args.llm_endpoint:
        raise RuntimeError("GENREC_ENDPOINT is required for LLM rerank.")

    if args.report:
        with open(args.report, "r", encoding="utf-8") as f:
            report = json.load(f)
    else:
        report = build_report(args.mode, args.state, proxy=args.proxy, hf_limit=None, gh_limit=None,
                              pushed_ids_path=args.pushed_ids)

    history_by_user = _extract_history(args.log, args.recent_days)

    # 并入种子用户：解决冷启动死锁（第一次没人点过按钮 → 没有用户 → 发不出去）。
    # 种子用户没有历史，topic_scores 全为 0，会拿到非个性化推荐；他们一旦点击，
    # 后续就转为个性化。已经有历史的种子用户不受影响（不覆盖其历史）。
    seed_users = [u.strip() for u in str(args.seed_users).split(",") if u.strip()]
    for u in seed_users:
        history_by_user.setdefault(u, {"recent": [], "older": []})
    candidates_by_source = _build_candidates(report)
    now = _now_utc()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for user_id, histories in history_by_user.items():
            recent = histories.get("recent", [])
            older = histories.get("older", [])
            topic_scores = _build_topic_scores(recent, now)
            older_summary = _summarize_history(older, args.llm_endpoint, args.llm_api_key, args.llm_model)

            results: list[dict[str, Any]] = []
            source_plan = [
                ("hf", args.hf_candidate_k, args.hf_top_k),
                ("gh", args.gh_candidate_k, args.gh_top_k),
                ("anthropic", args.anthropic_candidate_k, args.anthropic_top_k),
            ]
            for source, cand_k, top_k in source_plan:
                pool = candidates_by_source.get(source, [])
                if not pool:
                    continue
                if source == "anthropic" and top_k < 0:
                    results.extend(pool)
                    continue
                if top_k <= 0:
                    continue
                recalled = _select_by_rule(topic_scores, pool, cand_k)
                ranked = _rerank_by_llm(
                    recent,
                    older_summary,
                    recalled,
                    top_k,
                    args.llm_endpoint,
                    args.llm_api_key,
                    args.llm_model,
                )
                if not ranked:
                    ranked = recalled[:top_k]
                results.extend(ranked)

            record = {
                "user_id": user_id,
                "generated_at": report.get("generated_at"),
                "items": results,
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(json.dumps({"out": args.out, "users": len(history_by_user)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
