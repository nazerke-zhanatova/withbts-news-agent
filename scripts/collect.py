"""Collect new BTS news candidates from the sources in config/sources.yaml.

Writes state/candidates.json (only items not seen before, from the last 48 hours)
and downloads images from official and trusted sources into media/.
"""
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

from common import MEDIA, STATE, load_json, load_yaml, log, save_json, settings

MAX_AGE = timedelta(hours=48)
MAX_CANDIDATES = 30
UA = {"User-Agent": "withBTSkz-news-agent/1.0 (+https://withbts.kz)"}
POSTABLE = {"official", "trusted_fanbase"}


def safe_id(*parts):
    raw = "-".join(str(p) for p in parts)
    return re.sub(r"[^A-Za-z0-9_-]+", "_", raw)[:120]


def recent(ts):
    if ts is None:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - ts <= MAX_AGE


def passes(entry, text):
    """Keyword filter, matched as whole words (so "Jin" does not match "Seo Hyun Jin" style
    substrings like "Jinyoung", and "RM" does not match inside other words)."""
    words = [w.strip() for w in (entry.get("filter") or []) if w.strip()]
    if not words:
        return True
    return any(re.search(rf"(?<![A-Za-z0-9]){re.escape(w)}(?![A-Za-z0-9])", text) for w in words)


def download(url, cid, n=0):
    """Download one image. Returns the repo-relative path or None."""
    try:
        r = requests.get(url, headers=UA, timeout=30)
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "image" not in ctype:
            return None
        ext = ".png" if "png" in ctype else ".jpg"
        MEDIA.mkdir(exist_ok=True)
        path = MEDIA / f"{cid}_{n}{ext}"
        path.write_bytes(r.content)
        return f"media/{path.name}"
    except Exception as e:  # noqa: BLE001
        log(f"  image download failed: {e}")
        return None


# ---------------- Instagram (Business Discovery) ----------------

def collect_instagram(entries, cfg):
    token = os.environ.get("IG_ACCESS_TOKEN")
    ig_user = os.environ.get("IG_USER_ID")
    if not entries:
        return []
    if not (token and ig_user):
        log("Instagram sources skipped: IG_ACCESS_TOKEN or IG_USER_ID not set")
        return []
    ver = cfg.get("graph_api_version", "v23.0")
    out = []
    for e in entries:
        handle = e["handle"]
        limit = int(e.get("limit", 8))
        fields = (
            f"business_discovery.username({handle})"
            f"{{media.limit({limit}){{id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,"
            "children{media_type,media_url}}}"
        )
        try:
            r = requests.get(
                f"https://graph.facebook.com/{ver}/{ig_user}",
                params={"fields": fields, "access_token": token},
                timeout=30,
            )
            data = r.json()
            if "error" in data:
                log(f"Instagram @{handle}: {data['error'].get('message')}")
                continue
            media = data.get("business_discovery", {}).get("media", {}).get("data", [])
        except Exception as ex:  # noqa: BLE001
            log(f"Instagram @{handle} failed: {ex}")
            continue
        for m in media:
            ts = datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
            if not recent(ts) or not passes(e, m.get("caption", "") or ""):
                continue
            image_urls = []
            if m.get("media_type") == "CAROUSEL_ALBUM":
                for c in m.get("children", {}).get("data", []):
                    if c.get("media_type") == "IMAGE" and c.get("media_url"):
                        image_urls.append(c["media_url"])
            elif m.get("media_type") == "VIDEO" and m.get("thumbnail_url"):
                image_urls.append(m["thumbnail_url"])
            elif m.get("media_url"):
                image_urls.append(m["media_url"])
            out.append({
                "id": safe_id("ig", handle, m["id"]),
                "platform": "instagram",
                "handle": f"@{handle}",
                "tier": e.get("tier", "news_media"),
                "role": e.get("role", "original"),
                "url": m.get("permalink"),
                "text": m.get("caption", "") or "",
                "timestamp": ts.isoformat(),
                "_images": image_urls[:3],
            })
    return out


# ---------------- Telegram public channels ----------------

def collect_telegram(entries):
    if not entries:
        return []
    from bs4 import BeautifulSoup

    out = []
    for e in entries:
        handle = e["handle"].lstrip("@")
        try:
            r = requests.get(f"https://t.me/s/{handle}", headers=UA, timeout=30)
            r.raise_for_status()
        except Exception as ex:  # noqa: BLE001
            log(f"Telegram {handle} failed: {ex}")
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        for msg in soup.select("div.tgme_widget_message[data-post]"):
            post = msg["data-post"]
            t = msg.select_one("time[datetime]")
            ts = datetime.fromisoformat(t["datetime"]) if t else None
            if not recent(ts):
                continue
            text_el = msg.select_one(".tgme_widget_message_text")
            text = text_el.get_text("\n", strip=True) if text_el else ""
            if not passes(e, text):
                continue
            images = []
            for a in msg.select("a.tgme_widget_message_photo_wrap"):
                m = re.search(r"url\('([^']+)'\)", a.get("style", ""))
                if m:
                    images.append(m.group(1))
            out.append({
                "id": safe_id("tg", post),
                "platform": "telegram",
                "handle": f"@{handle}",
                "tier": e.get("tier", "news_media"),
                "role": e.get("role", "original"),
                "url": f"https://t.me/{post}",
                "text": text,
                "timestamp": ts.isoformat() if ts else None,
                "_images": images[:3],
            })
    return out


# ---------------- RSS ----------------

def collect_rss(entries):
    if not entries:
        return []
    import feedparser

    out = []
    for e in entries:
        try:
            feed = feedparser.parse(e["url"], agent=UA["User-Agent"])
        except Exception as ex:  # noqa: BLE001
            log(f"RSS {e.get('name')} failed: {ex}")
            continue
        for item in feed.entries[:30]:
            title = item.get("title", "")
            summary = re.sub(r"<[^>]+>", " ", item.get("summary", ""))
            if not passes(e, f"{title} {summary}"):
                continue
            ts = None
            for key in ("published", "updated"):
                if item.get(key):
                    try:
                        ts = parsedate_to_datetime(item[key])
                    except Exception:  # noqa: BLE001
                        try:
                            ts = datetime.fromisoformat(item[key].replace("Z", "+00:00"))
                        except Exception:  # noqa: BLE001
                            ts = None
                    break
            if not recent(ts):
                continue
            images = []
            for t in item.get("media_thumbnail", []) or []:
                if t.get("url"):
                    images.append(t["url"])
            out.append({
                "id": safe_id("rss", e.get("name", "feed"), item.get("id") or item.get("link")),
                "platform": "rss",
                "handle": e.get("name", "RSS"),
                "tier": e.get("tier", "news_media"),
                "role": e.get("role", "original"),
                "url": item.get("link"),
                "text": f"{title}\n\n{summary.strip()[:1500]}",
                "timestamp": ts.isoformat() if ts else None,
                "_images": images[:1],
            })
    return out


# ---------------- X (paid API) ----------------

def collect_x(cfg_x):
    if not cfg_x or not cfg_x.get("enabled"):
        return []
    bearer = os.environ.get("X_BEARER_TOKEN")
    if not bearer:
        log("X sources skipped: X_BEARER_TOKEN not set")
        return []
    h = {"Authorization": f"Bearer {bearer}"}
    out = []
    for e in cfg_x.get("accounts", []):
        handle = e["handle"]
        try:
            u = requests.get(f"https://api.x.com/2/users/by/username/{handle}", headers=h, timeout=30).json()
            uid = u["data"]["id"]
            r = requests.get(
                f"https://api.x.com/2/users/{uid}/tweets",
                headers=h,
                params={
                    "max_results": 10,
                    "exclude": "retweets,replies",
                    "tweet.fields": "created_at",
                    "expansions": "attachments.media_keys",
                    "media.fields": "url,type,preview_image_url",
                },
                timeout=30,
            ).json()
        except Exception as ex:  # noqa: BLE001
            log(f"X @{handle} failed: {ex}")
            continue
        media_map = {m["media_key"]: m for m in r.get("includes", {}).get("media", [])}
        for t in r.get("data", []):
            ts = datetime.fromisoformat(t["created_at"].replace("Z", "+00:00"))
            if not recent(ts) or not passes(e, t.get("text", "")):
                continue
            images = []
            for k in t.get("attachments", {}).get("media_keys", []):
                m = media_map.get(k, {})
                if m.get("url") or m.get("preview_image_url"):
                    images.append(m.get("url") or m.get("preview_image_url"))
            out.append({
                "id": safe_id("x", handle, t["id"]),
                "platform": "x",
                "handle": f"@{handle}",
                "tier": e.get("tier", "news_media"),
                "role": e.get("role", "original"),
                "url": f"https://x.com/{handle}/status/{t['id']}",
                "text": t.get("text", ""),
                "timestamp": ts.isoformat(),
                "_images": images[:3],
            })
    return out


def update_photo_library(new_items):
    """Remember photos from official and trusted posts for 7 days, so a later post
    about the same news can reuse a photo (with that account credited)."""
    lib = load_json(STATE / "photo_library.json", [])
    known = {e["path"] for e in lib}
    for item in new_items:
        for path in item.get("media", []):
            if path not in known:
                lib.append({
                    "path": path,
                    "credit": item["handle"].lstrip("@"),
                    "tier": item["tier"],
                    "source_url": item.get("url"),
                    "text": (item.get("text") or "")[:400],
                    "timestamp": item.get("timestamp"),
                })
    lib = [e for e in lib if (MEDIA.parent / e["path"]).exists()][-300:]
    save_json(STATE / "photo_library.json", lib)


def main():
    cfg = settings()
    if not cfg.get("enabled", True):
        log("Kill switch is off (enabled: false). Nothing collected.")
        save_json(STATE / "candidates.json", [])
        return 0

    sources = load_yaml("sources.yaml")
    seen = set(load_json(STATE / "seen.json", []))

    items = []
    items += collect_instagram(sources.get("instagram") or [], cfg)
    items += collect_telegram(sources.get("telegram") or [])
    items += collect_rss(sources.get("rss") or [])
    items += collect_x(sources.get("x") or {})

    new = [i for i in items if i["id"] not in seen]
    tier_rank = {"official": 0, "trusted_fanbase": 1, "news_media": 2}
    new.sort(key=lambda i: (tier_rank.get(i["tier"], 3), i.get("timestamp") or ""))
    new = new[:MAX_CANDIDATES]

    for item in new:
        urls = item.pop("_images", [])
        item["media"] = []
        if item["tier"] in POSTABLE:
            for n, u in enumerate(urls):
                p = download(u, item["id"], n)
                if p:
                    item["media"].append(p)

    save_json(STATE / "candidates.json", new)
    update_photo_library(new)
    log(f"Collected {len(items)} items, {len(new)} new candidates.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
