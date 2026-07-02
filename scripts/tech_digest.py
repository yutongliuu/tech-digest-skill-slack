#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Fetch Anthropic news, HuggingFace daily papers, GitHub trending.
Output JSON with diffs against prior state.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime, timezone, date, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}

DEFAULT_PROXY = os.environ.get("TECH_DIGEST_PROXY", "")

# HuggingFace base URL. Set HF_ENDPOINT=https://hf-mirror.com to use the
# China-accessible mirror when the official site is unreachable (no proxy needed).
HF_ENDPOINT = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")


def _get_session(proxy: str | None = None) -> requests.Session:
    session = requests.Session()
    if proxy:
        session.trust_env = False
        session.proxies.update({"http": proxy, "https": proxy})
    return session


DEFAULT_SESSION = _get_session(DEFAULT_PROXY or None)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_state(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_state(path: str, state: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _diff_ids(current: list[str], previous: list[str]) -> dict[str, list[str]]:
    prev_set = set(previous)
    cur_set = set(current)
    return {
        "new": [i for i in current if i not in prev_set],
        "same": [i for i in current if i in prev_set],
        "dropped": [i for i in previous if i not in cur_set],
    }


def _tag_items(items: list[dict[str, Any]], prev_ids: list[str]) -> list[dict[str, Any]]:
    prev_set = set(prev_ids)
    for item in items:
        item["status"] = "new" if item.get("id") not in prev_set else "same"
    return items


def _title_from_slug(slug: str) -> str:
    slug = slug.strip("/").split("/")[-1]
    if not slug:
        return "Anthropic News"
    return slug.replace("-", " ")[:160]


def _extract_anthropic_urls(text: str) -> list[str]:
    urls = re.findall(r"https?://www\.anthropic\.com/news/[^\s)\]\"']+", text)
    if urls:
        return urls
    paths = re.findall(r"/news/[A-Za-z0-9\-_/]+", text)
    return [f"https://www.anthropic.com{p}" for p in paths]


def _parse_date(text: str) -> date | None:
    text = " ".join(text.split())
    # ISO-like dates
    m = re.search(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", text)
    if m:
        y, mo, d = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    # Month name formats like "Feb 17, 2026"
    m = re.search(
        r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(\d{1,2}),\s+(20\d{2})\b",
        text,
        re.IGNORECASE,
    )
    if m:
        month_map = {
            "jan": 1,
            "feb": 2,
            "mar": 3,
            "apr": 4,
            "may": 5,
            "jun": 6,
            "jul": 7,
            "aug": 8,
            "sep": 9,
            "oct": 10,
            "nov": 11,
            "dec": 12,
        }
        month_key = m.group(1)[:3].lower()
        mo = month_map.get(month_key)
        if mo:
            try:
                return date(int(m.group(3)), mo, int(m.group(2)))
            except ValueError:
                return None
    return None


def _parse_date_any(text: str) -> date | None:
    if not text:
        return None
    s = text.strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(s).date()
    except Exception:
        pass
    return _parse_date(s)


def _discover_feed_url(html_text: str, base_url: str) -> str | None:
    try:
        soup = BeautifulSoup(html_text, "html.parser")
        for link in soup.find_all("link"):
            rel = link.get("rel")
            if isinstance(rel, list):
                rel = " ".join(rel)
            rel = (rel or "").lower()
            if "alternate" not in rel:
                continue
            type_ = (link.get("type") or "").lower()
            if "rss" in type_ or "atom" in type_ or "xml" in type_:
                href = link.get("href")
                if href:
                    return urljoin(base_url, href)
    except Exception:
        return None
    return None


def _strip_ns(tag: str) -> str:
    return tag.split("}")[-1]


def _parse_feed_items(xml_text: str, limit: int) -> list[dict[str, Any]]:
    try:
        import xml.etree.ElementTree as ET
    except Exception:
        return []

    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return []

    def child_text(node, names: set[str]) -> str | None:
        for child in list(node):
            if _strip_ns(child.tag) in names and child.text:
                return child.text.strip()
        return None

    def child_link(node) -> str | None:
        for child in list(node):
            if _strip_ns(child.tag) == "link":
                href = child.get("href")
                if href:
                    return href.strip()
                if child.text:
                    return child.text.strip()
        return None

    items: list[dict[str, Any]] = []
    # RSS-style items
    for item in root.iter():
        if _strip_ns(item.tag) == "item":
            title = child_text(item, {"title"}) or ""
            link = child_link(item) or ""
            date_text = child_text(item, {"pubDate", "date", "published", "updated"})
            pub_date = _parse_date_any(date_text) if date_text else None
            guid = child_text(item, {"guid", "id"}) or link or title
            items.append(
                {
                    "id": guid,
                    "title": title,
                    "url": link,
                    "published_date": pub_date.isoformat() if pub_date else None,
                }
            )
            if len(items) >= limit:
                return items

    # Atom-style entries
    for entry in root.iter():
        if _strip_ns(entry.tag) == "entry":
            title = child_text(entry, {"title"}) or ""
            link = child_link(entry) or ""
            date_text = child_text(entry, {"published", "updated", "date"})
            pub_date = _parse_date_any(date_text) if date_text else None
            entry_id = child_text(entry, {"id"}) or link or title
            items.append(
                {
                    "id": entry_id,
                    "title": title,
                    "url": link,
                    "published_date": pub_date.isoformat() if pub_date else None,
                }
            )
            if len(items) >= limit:
                return items

    return items


def _fetch_with_fallback(
    url: str,
    timeout: int = 15,
    session: requests.Session | None = None,
) -> str:
    session = session or DEFAULT_SESSION
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            res = session.get(url, headers=UA, timeout=timeout)
            res.raise_for_status()
            return res.text
        except Exception as e:
            last_error = e
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))
                continue
            break
    if "anthropic.com" not in url:
        if last_error:
            raise last_error
        raise RuntimeError("fetch failed")
    fallback = os.environ.get("ANTHROPIC_NEWS_FALLBACK", "https://r.jina.ai/http://www.anthropic.com/news")
    # If fallback is a base for news list, attempt to map specific article URLs too.
    if "r.jina.ai" in fallback and url.startswith("https://www.anthropic.com/"):
        proxied = "https://r.jina.ai/http://www.anthropic.com" + url.replace("https://www.anthropic.com", "")
    else:
        proxied = fallback
    res = session.get(proxied, headers=UA, timeout=timeout + 5)
    res.raise_for_status()
    return res.text


def _extract_published_date_from_page(html_text: str) -> date | None:
    # Try structured HTML first
    try:
        soup = BeautifulSoup(html_text, "html.parser")
        for meta in soup.find_all("meta"):
            name = (meta.get("property") or meta.get("name") or "").lower()
            if name in {"article:published_time", "published_time", "pubdate", "date"}:
                content = meta.get("content", "")
                if content:
                    # Try parsing ISO datetime
                    content = content.replace("Z", "+00:00")
                    try:
                        return datetime.fromisoformat(content).date()
                    except ValueError:
                        d = _parse_date_any(content)
                        if d:
                            return d
        time_tag = soup.find("time")
        if time_tag:
            dt = time_tag.get("datetime", "") or time_tag.get_text(" ")
            d = _parse_date_any(dt)
            if d:
                return d
    except Exception:
        pass
    # Fallback: regex over text
    return _parse_date_any(html_text)


def _today_shanghai() -> date:
    # 用固定 +8 偏移算"中国今天"，不依赖 ZoneInfo("Asia/Shanghai")。
    # Windows 默认不带 IANA 时区数据库，ZoneInfo 会抛
    # "No time zone found with key Asia/Shanghai"，导致 Anthropic 抓取整段失败。
    # 中国不使用夏令时，固定 UTC+8 永远正确。
    return datetime.now(timezone(timedelta(hours=8))).date()


def fetch_anthropic(limit: int = 3, session: requests.Session | None = None) -> list[dict[str, Any]]:
    url = "https://www.anthropic.com/news"
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    session = session or DEFAULT_SESSION

    try:
        html = _fetch_with_fallback(url, timeout=15, session=session)

        # Prefer official RSS/Atom if discoverable
        feed_url = _discover_feed_url(html, url)
        if feed_url:
            try:
                feed_xml = _fetch_with_fallback(feed_url, timeout=15, session=session)
                feed_items = _parse_feed_items(feed_xml, limit=limit)
                if feed_items:
                    for it in feed_items:
                        link = it.get("url") or ""
                        title = it.get("title") or _title_from_slug(link)
                        item_id = it.get("id") or link or title
                        if link.startswith("https://www.anthropic.com"):
                            item_id = link.replace("https://www.anthropic.com", "")
                        items.append(
                            {
                                "id": item_id,
                                "title": title[:160],
                                "url": link,
                                "published_date": it.get("published_date"),
                            }
                        )
                    return items[:limit]
            except Exception:
                pass

        soup = BeautifulSoup(html, "html.parser")
        links = soup.find_all("a", href=re.compile(r"^/news/"))
        for link in links:
            href = link.get("href", "")
            if not href or href in seen:
                continue
            seen.add(href)
            text = " ".join(link.get_text(" ").split())
            if len(text) < 10:
                continue
            pub_date = _parse_date(text)
            if not pub_date:
                # Try to fetch article page for date
                try:
                    article_url = f"https://www.anthropic.com{href}"
                    article_html = _fetch_with_fallback(article_url, timeout=15, session=session)
                    pub_date = _extract_published_date_from_page(article_html)
                except Exception:
                    pub_date = None
            items.append(
                {
                    "id": href,
                    "title": text[:160],
                    "url": f"https://www.anthropic.com{href}",
                    "published_date": pub_date.isoformat() if pub_date else None,
                }
            )
            if len(items) >= limit:
                return items
    except Exception:
        # Last-resort fallback: parse proxied text
        fallback = os.environ.get("ANTHROPIC_NEWS_FALLBACK", "https://r.jina.ai/http://www.anthropic.com/news")
        res = session.get(fallback, headers=UA, timeout=20)
        res.raise_for_status()
        urls = _extract_anthropic_urls(res.text)
        for u in urls:
            if u in seen:
                continue
            seen.add(u)
            title = _title_from_slug(u)
            items.append(
                {
                    "id": u.replace("https://www.anthropic.com", ""),
                    "title": title,
                    "url": u,
                    "published_date": None,
                }
            )
            if len(items) >= limit:
                break

    return items


def fetch_hf_daily_papers(
    limit: int | None = 3,
    timeout: int = 20,
    retries: int = 3,
    backoff_seconds: float = 1.0,
    session: requests.Session | None = None,
) -> list[dict[str, Any]]:
    url = f"{HF_ENDPOINT}/api/daily_papers"
    session = session or DEFAULT_SESSION
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            res = session.get(url, timeout=timeout)
            res.raise_for_status()
            data = res.json() or []
            break
        except Exception as e:
            last_error = e
            if attempt < retries - 1:
                time.sleep(backoff_seconds * (attempt + 1))
                continue
            raise

    items: list[dict[str, Any]] = []
    papers = data if limit is None else data[:limit]
    for p in papers:
        paper = p.get("paper", {}) or {}
        pid = paper.get("id") or paper.get("_id") or paper.get("title", "")
        title = paper.get("title", "Unknown Title")
        summary = paper.get("summary", "")
        summary = summary.strip()
        if len(summary) > 240:
            summary = summary[:240] + "..."
        items.append(
            {
                "id": pid,
                "title": title,
                "summary": summary,
                "url": f"{HF_ENDPOINT}/papers/{pid}" if pid else f"{HF_ENDPOINT}/papers",
            }
        )
    return items


def fetch_github_trending(
    limit: int | None = 3,
    timeout: int = 25,
    retries: int = 3,
    backoff_seconds: float = 1.0,
    session: requests.Session | None = None,
) -> list[dict[str, Any]]:
    url = "https://github.com/trending"
    session = session or DEFAULT_SESSION
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            res = session.get(url, headers=UA, timeout=timeout)
            res.raise_for_status()
            break
        except Exception as e:
            last_error = e
            if attempt < retries - 1:
                time.sleep(backoff_seconds * (attempt + 1))
                continue
            raise
    soup = BeautifulSoup(res.text, "html.parser")
    rows = soup.find_all("article", class_="Box-row")

    items: list[dict[str, Any]] = []
    rows_iter = rows if limit is None else rows[:limit]
    for row in rows_iter:
        h2 = row.find("h2", class_="h3 lh-condensed")
        if not h2:
            continue
        a_tag = h2.find("a")
        if not a_tag:
            continue
        repo_text = " ".join(a_tag.get_text(" ").split())
        repo = repo_text.replace(" / ", "/").replace(" ", "")
        href = a_tag.get("href", "")
        url_full = f"https://github.com{href}" if href else "https://github.com/trending"
        p_tag = row.find("p", class_="col-9 color-fg-muted my-1 pr-4")
        description = " ".join(p_tag.get_text(" ").split()) if p_tag else "无描述"
        items.append(
            {
                "id": repo,
                "repository": repo,
                "description": description,
                "url": url_full,
            }
        )
    return items


def build_report(
    mode: str,
    state_path: str,
    proxy: str | None = None,
    hf_limit: int | None = 1,
    gh_limit: int | None = 1,
    pushed_ids_path: str | None = None,
) -> dict[str, Any]:
    state = _load_state(state_path)
    prev_an = state.get("anthropic", {})
    prev_hf = state.get("hf", {})
    prev_gh = state.get("gh", {})
    session = _get_session(proxy) if proxy else DEFAULT_SESSION

    # 已推送过的 item id（全局，跨源跨天累积）。去重以"推送过的"为准：
    # 只有真正推给用户看过的才算 seen，仅仅被抓取到、但没推送的下次仍有机会。
    # 若未提供 pushed_ids 文件，退回旧行为（用抓取快照 prev_ids 判重）。
    use_pushed = pushed_ids_path is not None
    pushed_ids: set[str] = set()
    if use_pushed and os.path.exists(pushed_ids_path):
        try:
            with open(pushed_ids_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            pushed_ids = set(data.get("ids", []) if isinstance(data, dict) else data)
        except Exception:
            pushed_ids = set()

    out: dict[str, Any] = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "generated_at": _now_iso(),
    }

    if mode in ("anthropic", "all"):
        try:
            items = fetch_anthropic(session=session)
            today = _today_shanghai().isoformat()
            today_items = [i for i in items if i.get("published_date") == today]
            ids = [i["id"] for i in today_items]
            prev_ids = prev_an.get("ids", []) if isinstance(prev_an, dict) else []
            seen_ids = list(pushed_ids) if use_pushed else prev_ids
            diff = _diff_ids(ids, seen_ids)
            has_update = len(ids) > 0
            today_items = _tag_items(today_items, seen_ids)
            out["anthropic"] = {
                "items": today_items,
                "diff": diff,
                "has_update": has_update,
                "today": today,
            }
            state["anthropic"] = {"ids": ids, "items": today_items, "today": today}
        except Exception as e:
            out["anthropic"] = {"error": str(e), "items": [], "diff": {}, "has_update": False}

    if mode in ("daily", "all"):
        # HuggingFace
        try:
            items = fetch_hf_daily_papers(limit=hf_limit, session=session)
            ids = [i["id"] for i in items]
            prev_ids = prev_hf.get("ids", []) if isinstance(prev_hf, dict) else []
            seen_ids = list(pushed_ids) if use_pushed else prev_ids
            diff = _diff_ids(ids, seen_ids)
            items = _tag_items(items, seen_ids)
            out["hf"] = {"items": items, "diff": diff}
            state["hf"] = {"ids": ids, "items": items}
        except Exception as e:
            out["hf"] = {"error": str(e), "items": [], "diff": {}}

        # GitHub
        try:
            items = fetch_github_trending(limit=gh_limit, session=session)
            ids = [i["id"] for i in items]
            prev_ids = prev_gh.get("ids", []) if isinstance(prev_gh, dict) else []
            seen_ids = list(pushed_ids) if use_pushed else prev_ids
            diff = _diff_ids(ids, seen_ids)
            items = _tag_items(items, seen_ids)
            out["gh"] = {"items": items, "diff": diff}
            state["gh"] = {"ids": ids, "items": items}
        except Exception as e:
            out["gh"] = {"error": str(e), "items": [], "diff": {}}

    state["updated_at"] = out["generated_at"]
    _save_state(state_path, state)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["anthropic", "daily", "all"], required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--proxy", default=os.environ.get("TECH_DIGEST_PROXY"))
    parser.add_argument("--hf-limit", type=int, default=1)
    parser.add_argument("--gh-limit", type=int, default=1)
    args = parser.parse_args()

    report = build_report(
        args.mode,
        args.state,
        proxy=args.proxy,
        hf_limit=None if args.hf_limit < 0 else args.hf_limit,
        gh_limit=None if args.gh_limit < 0 else args.gh_limit,
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
