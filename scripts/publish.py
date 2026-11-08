"""Validate every post in queue/, add credits and hashtags, and publish.

Rules enforced here (the agent cannot skip them):
  - kill switch (enabled: false) stops everything
  - posts without credits are rejected
  - only official or trusted_fanbase sources
  - images must come from media/ (collected from that source) or be the rendered card
"""
import os
import re
import shutil
import sys
import time
import unicodedata
from pathlib import Path

import requests


# Safety lock: this agent only CREATES posts. Any DELETE request is refused, so nothing
# already on the withBTS.kz accounts (everything before 2 November 2026) can be removed.
def _no_delete(*args, **kwargs):
    raise RuntimeError("Blocked: the agent is not allowed to delete anything.")


_original_request = requests.Session.request


def _guarded_request(self, method, url, *args, **kwargs):
    if str(method).upper() == "DELETE" or "method=delete" in str(url).lower():
        _no_delete()
    data = kwargs.get("data") or {}
    if isinstance(data, dict) and str(data.get("method", "")).lower() == "delete":
        _no_delete()
    return _original_request(self, method, url, *args, **kwargs)


requests.delete = _no_delete
requests.Session.request = _guarded_request

from common import QUEUE, ROOT, STATE, alert, load_json, log, now_almaty, save_json, settings

LIMITS = {"telegram": 4000, "instagram": 2200, "threads": 500, "x": 280}
REQUIRED = ["id", "credits", "card", "telegram", "instagram", "threads", "x", "source_tier"]


# ---------------- validation and composing ----------------

# ---------------- photo memory: never use the same photo twice ----------------

USED_PHOTOS = STATE / "used_photos.json"


def photo_hash(path):
    """Small "difference hash": the same photo gives (almost) the same hash even after
    resizing or recompressing, so a photo downloaded again from another site is still caught."""
    from PIL import Image
    img = Image.open(path).convert("L").resize((17, 16))
    px = list(img.getdata())
    bits = [px[r * 17 + c] > px[r * 17 + c + 1] for r in range(16) for c in range(16)]
    return f"{int(''.join('1' if b else '0' for b in bits), 2):064x}"


def same_photo(h1, h2, limit=40):
    return bin(int(h1, 16) ^ int(h2, 16)).count("1") <= limit


def used_photos():
    used = load_json(USED_PHOTOS, [])
    known = {u["hash"] for u in used}
    # also learn from earlier posts whose photos are still in media/
    for a in sorted((STATE / "archive").glob("*.json")):
        old = load_json(a, {})
        for rel in [old.get("image")] + list(old.get("carousel") or []):
            p = ROOT / rel if rel else None
            if p and p.exists():
                try:
                    h = photo_hash(p)
                except Exception:  # noqa: BLE001
                    continue
                if h not in known:
                    used.append({"hash": h, "post": old.get("id"), "file": rel, "url": old.get("image_url")})
                    known.add(h)
    return used


def photo_reused(post, used):
    paths = [post.get("image")] + list(post.get("carousel") or [])
    for rel in paths:
        if not rel or not (ROOT / rel).exists():
            continue
        h = photo_hash(ROOT / rel)
        for u in used:
            if u.get("post") != post.get("id") and same_photo(h, u["hash"]):
                return f"photo {rel} was already used in post {u.get('post')}"
    return None


def remember_photos(post):
    used = used_photos()
    for rel in [post.get("image")] + list(post.get("carousel") or []):
        if rel and (ROOT / rel).exists():
            used.append({"hash": photo_hash(ROOT / rel), "post": post.get("id"), "file": rel,
                         "url": post.get("image_url") if rel == post.get("image") else None,
                         "date": now_almaty().isoformat(timespec="minutes")})
    save_json(USED_PHOTOS, used[-3000:])


def validate(post):
    for key in REQUIRED:
        if not post.get(key):
            return f"missing field: {key}"
    credits = [c for c in post["credits"] if isinstance(c, str) and c.strip()]
    if not credits:
        return "no credits"
    if post["source_tier"] not in ("official", "trusted_fanbase", "news_media"):
        return f"source tier not allowed: {post['source_tier']}"
    img = post.get("image")
    if not img:
        return "no photo (every post must have a photo in the design)"
    if img:
        p = (ROOT / img).resolve()
        if not str(p).startswith(str((ROOT / "media").resolve())) or not p.exists():
            return f"image not from collected media: {img}"
        ic = (post.get("image_credit") or "").strip().lstrip("@")
        if not ic:
            return "photo has no image_credit"
        if ic.lower() not in ("-", "none") and ic.lower() not in [c.strip().lstrip("@").lower() for c in credits]:
            post["credits"].append(ic)  # photo owner always appears in the credits line
        reused = photo_reused(post, used_photos())
        if reused:
            return reused + " (never use the same photo twice)"
    for c in post.get("carousel") or []:
        cp = (ROOT / c).resolve()
        if not str(cp).startswith(str((ROOT / "media").resolve())):
            return f"carousel photo not from collected media: {c}"
    if not post.get("card_file") or not (ROOT / post["card_file"]).exists():
        return "card image not rendered"
    for k in ("telegram", "instagram", "threads"):
        tail = "\n".join(post[k].strip().splitlines()[-2:])
        if "?" not in tail:
            return f"{k} text does not end with a question to ARMY"
    for k in ("telegram", "instagram", "threads", "x"):
        if "credits" in post[k].lower():
            return f"{k} text contains its own credits line (script adds it)"
        if re.search(r"(^|\s)@[A-Za-z0-9_.]", post[k]):
            return f"{k} text contains an @mention (not allowed, could tag a wrong account)"
    return None


def x_len(text):
    # X counts most CJK characters (like Korean) as 2
    n = 0
    for ch in text:
        n += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return n


def compose(post, platform, cfg):
    body = post[platform].strip()
    names = []
    for c in post["credits"]:
        n = c.strip().lstrip("@").strip()
        if n and n not in names:
            names.append(n)
    credits_line = cfg.get("credits_template", "credits: {credits}").format(credits=", ".join(names))
    tags = " ".join(cfg.get("hashtags", {}).get(platform, []))
    measure = x_len if platform == "x" else len
    limit = LIMITS[platform]

    full = f"{body}\n\n{credits_line}" + (f"\n\n{tags}" if tags else "")
    if measure(full) <= limit:
        return full
    full = f"{body}\n\n{credits_line}"  # drop hashtags first, never the credits
    if measure(full) <= limit:
        return full
    room = limit - measure(f"...\n\n{credits_line}")
    cut = body
    while measure(cut) > room:
        cut = cut[:-1]
    return f"{cut.rstrip()}...\n\n{credits_line}"


def public_url(rel_path, cfg):
    repo = cfg.get("public_repo")
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    if not repo:
        raise RuntimeError("public_repo unknown, cannot build image URL")
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{rel_path}"


# ---------------- platforms ----------------

def post_telegram(text, card, cfg, slides=None):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHANNEL_ID")
    if not (token and chat):
        raise RuntimeError("TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID not set")
    api = f"https://api.telegram.org/bot{token}"

    def send_text():
        r = requests.post(f"{api}/sendMessage", data={"chat_id": chat, "text": text, "disable_web_page_preview": True}, timeout=60)
        r.raise_for_status()
        return str(r.json()["result"]["message_id"])

    if card is None:  # no photo: text only
        return send_text()
    images = [card] + list(slides or [])
    if len(images) > 1:  # album: cover card + photos, caption on the first one
        import json as _json

        files, media = {}, []
        for i, path in enumerate(images[:10]):
            key = f"photo{i}"
            files[key] = open(ROOT / path, "rb")
            item = {"type": "photo", "media": f"attach://{key}"}
            if i == 0 and len(text) <= 1024:
                item["caption"] = text
            media.append(item)
        try:
            r = requests.post(f"{api}/sendMediaGroup", data={"chat_id": chat, "media": _json.dumps(media)}, files=files, timeout=120)
            r.raise_for_status()
            first_id = str(r.json()["result"][0]["message_id"])
        finally:
            for fh in files.values():
                fh.close()
        if len(text) > 1024:
            send_text()
        return first_id
    with open(ROOT / card, "rb") as f:
        if len(text) <= 1024:
            r = requests.post(f"{api}/sendPhoto", data={"chat_id": chat, "caption": text}, files={"photo": f}, timeout=60)
            r.raise_for_status()
            return str(r.json()["result"]["message_id"])
        r = requests.post(f"{api}/sendPhoto", data={"chat_id": chat}, files={"photo": f}, timeout=60)
        r.raise_for_status()
    return send_text()


def _ig_wait(base, cid, token):
    for _ in range(24):
        st = requests.get(f"{base}/{cid}", params={"fields": "status_code", "access_token": token}, timeout=30).json()
        if st.get("status_code") == "FINISHED":
            return
        if st.get("status_code") == "ERROR":
            raise RuntimeError(f"container error: {st}")
        time.sleep(5)


def post_instagram(text, card, cfg, slides=None):
    token, ig_user = os.environ.get("IG_ACCESS_TOKEN"), os.environ.get("IG_USER_ID")
    if not (token and ig_user):
        raise RuntimeError("IG_ACCESS_TOKEN or IG_USER_ID not set")
    base = f"https://graph.facebook.com/{cfg.get('graph_api_version', 'v23.0')}"
    images = [card] + list(slides or [])
    if len(images) == 1:
        r = requests.post(f"{base}/{ig_user}/media",
                          data={"image_url": public_url(card, cfg), "caption": text, "access_token": token}, timeout=60).json()
    else:  # carousel: one container per image, then one carousel container
        children = []
        for path in images[:10]:
            c = requests.post(f"{base}/{ig_user}/media", data={
                "image_url": public_url(path, cfg), "is_carousel_item": "true", "access_token": token}, timeout=60).json()
            if "id" not in c:
                raise RuntimeError(f"carousel item failed ({path}): {c}")
            _ig_wait(base, c["id"], token)
            children.append(c["id"])
        r = requests.post(f"{base}/{ig_user}/media", data={
            "media_type": "CAROUSEL", "children": ",".join(children), "caption": text, "access_token": token}, timeout=60).json()
    if "id" not in r:
        raise RuntimeError(f"container failed: {r}")
    _ig_wait(base, r["id"], token)
    r = requests.post(f"{base}/{ig_user}/media_publish", data={"creation_id": r["id"], "access_token": token}, timeout=60).json()
    if "id" not in r:
        raise RuntimeError(f"publish failed: {r}")
    return r["id"]


def post_threads(text, card, cfg, slides=None):
    token = os.environ.get("THREADS_ACCESS_TOKEN")
    user = os.environ.get("THREADS_USER_ID") or "me"   # "me" works with a Threads user token
    if not token:
        raise RuntimeError("THREADS_ACCESS_TOKEN not set")
    base = "https://graph.threads.net/v1.0"
    images = [card] + list(slides or [])
    if len(images) == 1:
        r = requests.post(f"{base}/{user}/threads", data={
            "media_type": "IMAGE", "image_url": public_url(card, cfg), "text": text, "access_token": token}, timeout=60).json()
    else:
        children = []
        for path in images[:10]:
            c = requests.post(f"{base}/{user}/threads", data={
                "media_type": "IMAGE", "image_url": public_url(path, cfg), "is_carousel_item": "true",
                "access_token": token}, timeout=60).json()
            if "id" not in c:
                raise RuntimeError(f"carousel item failed ({path}): {c}")
            children.append(c["id"])
        time.sleep(10)
        r = requests.post(f"{base}/{user}/threads", data={
            "media_type": "CAROUSEL", "children": ",".join(children), "text": text, "access_token": token}, timeout=60).json()
    if "id" not in r:
        raise RuntimeError(f"container failed: {r}")
    time.sleep(30)  # Meta recommends waiting before publishing
    r = requests.post(f"{base}/{user}/threads_publish", data={"creation_id": r["id"], "access_token": token}, timeout=60).json()
    if "id" not in r:
        raise RuntimeError(f"publish failed: {r}")
    return r["id"]


# ---------------- video posts (CLAUDE.md rule 5c) ----------------

def video_telegram(text, video, video_url, cfg):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHANNEL_ID")
    if not (token and chat):
        raise RuntimeError("TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID not set")
    api = f"https://api.telegram.org/bot{token}"
    data = {"chat_id": chat, "supports_streaming": "true"}
    if len(text) <= 1024:
        data["caption"] = text
    with open(ROOT / video, "rb") as f:
        r = requests.post(f"{api}/sendVideo", data=data, files={"video": f}, timeout=300)
    r.raise_for_status()
    first = str(r.json()["result"]["message_id"])
    if len(text) > 1024:
        requests.post(f"{api}/sendMessage", data={"chat_id": chat, "text": text, "disable_web_page_preview": True},
                      timeout=60).raise_for_status()
    return first


def video_instagram(text, video, video_url, cfg):
    token, ig_user = os.environ.get("IG_ACCESS_TOKEN"), os.environ.get("IG_USER_ID")
    if not (token and ig_user):
        raise RuntimeError("IG_ACCESS_TOKEN or IG_USER_ID not set")
    base = f"https://graph.facebook.com/{cfg.get('graph_api_version', 'v23.0')}"
    url = video_url or public_url(video, cfg)
    r = requests.post(f"{base}/{ig_user}/media", data={
        "media_type": "REELS", "video_url": url, "caption": text, "share_to_feed": "true",
        "access_token": token}, timeout=60).json()
    if "id" not in r:
        raise RuntimeError(f"reel container failed: {r}")
    for _ in range(60):  # videos take a few minutes to process
        st = requests.get(f"{base}/{r['id']}", params={"fields": "status_code,status", "access_token": token}, timeout=30).json()
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") == "ERROR":
            raise RuntimeError(f"reel processing error: {st}")
        time.sleep(10)
    p = requests.post(f"{base}/{ig_user}/media_publish", data={"creation_id": r["id"], "access_token": token}, timeout=60).json()
    if "id" not in p:
        raise RuntimeError(f"reel publish failed: {p}")
    return p["id"]


def video_threads(text, video, video_url, cfg):
    token = os.environ.get("THREADS_ACCESS_TOKEN")
    user = os.environ.get("THREADS_USER_ID") or "me"
    if not token:
        raise RuntimeError("THREADS_ACCESS_TOKEN not set")
    base = "https://graph.threads.net/v1.0"
    url = video_url or public_url(video, cfg)
    r = requests.post(f"{base}/{user}/threads", data={
        "media_type": "VIDEO", "video_url": url, "text": text, "access_token": token}, timeout=60).json()
    if "id" not in r:
        raise RuntimeError(f"video container failed: {r}")
    for _ in range(60):
        st = requests.get(f"{base}/{r['id']}", params={"fields": "status,error_message", "access_token": token}, timeout=30).json()
        if st.get("status") == "FINISHED":
            break
        if st.get("status") == "ERROR":
            raise RuntimeError(f"video processing error: {st}")
        time.sleep(10)
    p = requests.post(f"{base}/{user}/threads_publish", data={"creation_id": r["id"], "access_token": token}, timeout=60).json()
    if "id" not in p:
        raise RuntimeError(f"video publish failed: {p}")
    return p["id"]


def post_x(text, card, cfg, slides=None):
    keys = [os.environ.get(k) for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")]
    if not all(keys):
        raise RuntimeError("X API keys not set")
    from requests_oauthlib import OAuth1

    auth = OAuth1(*keys)
    # Text post. Image upload on X is left out on purpose: its media API has changed often.
    r = requests.post("https://api.x.com/2/tweets", json={"text": text}, auth=auth, timeout=60)
    if r.status_code >= 300:
        raise RuntimeError(f"{r.status_code}: {r.text[:300]}")
    return r.json()["data"]["id"]


PUBLISHERS = {"telegram": post_telegram, "instagram": post_instagram, "threads": post_threads, "x": post_x}
VIDEO_PUBLISHERS = {"telegram": video_telegram, "instagram": video_instagram, "threads": video_threads}
META_GAP = 130          # seconds between two Instagram (or two Threads) posts in one run
LAST_META = {}


# ---------------- main ----------------

def main():
    cfg = settings()
    if not cfg.get("enabled", True):
        log("Kill switch is off (enabled: false). Nothing published.")
        return 0
    approved_run = os.environ.get("PUBLISH_PENDING") == "1"   # Nazerke said "ready, go"
    approval_mode = bool(cfg.get("approval_mode", False))
    dry = cfg.get("dry_run", True) and not approved_run
    all_platforms = [p for p, on in (cfg.get("platforms") or {}).items() if on]
    posted = load_json(STATE / "posted.json", [])
    archive = STATE / "archive"
    rejected = STATE / "rejected"
    pending = STATE / "pending"
    limit = int(cfg.get("max_posts_per_run", 3))

    if approved_run:
        files = sorted(pending.glob("*.json"))
        log(f"Publishing {len(files)} approved posts to: {', '.join(all_platforms)}")
    else:
        files = sorted(QUEUE.glob("*.json"))[:limit]
    if not files:
        log("Nothing to publish.")
    for f in files:
        post = load_json(f, None)
        if not post:
            continue
        # a post can be limited to some platforms (for example a repost on Instagram and Threads only)
        platforms = [p for p in all_platforms if not post.get("only_platforms") or p in post["only_platforms"]]
        reason = validate(post)
        if reason:
            log(f"REJECTED {post.get('id')}: {reason}")
            alert(f"🚫 withBTS.kz: a post was not published.\n\n{post.get('card', {}).get('headline', post.get('id'))}\n\nReason: {reason}")
            post["rejected_reason"] = reason
            save_json(rejected / f.name, post)
            f.unlink()
            continue

        if (approval_mode or post.get("needs_approval")) and not approved_run:
            # Wait for Nazerke's approval: save the post and readable previews, publish nothing.
            pending.mkdir(parents=True, exist_ok=True)
            save_json(pending / f.name, post)
            for p in platforms:
                (pending / f"{post['id']}.{p}.txt").write_text(compose(post, p, cfg), encoding="utf-8")
            posted.append({
                "id": post["id"], "time": now_almaty().isoformat(timespec="minutes"),
                "headline": post["card"].get("headline"), "category": post.get("category"),
                "source_urls": post.get("source_urls", []), "credits": post["credits"],
                "status": "waiting for approval",
            })
            f.unlink()
            log(f"Waiting for approval: {post['id']}")
            if post.get("needs_approval"):
                alert("⚠️ withBTS.kz: a sensitive post is waiting for your approval.\n\n"
                      f"{post['card'].get('headline', '')}\n\n"
                      f"{post.get('telegram', '')[:600]}\n\n"
                      "Nothing was published. Open Claude and say \"ready, go\" to publish it, or ignore it.")
            continue

        results = {}
        has_photo = bool(post.get("image"))
        for p in platforms:
            if not has_photo and p in ("instagram", "threads"):
                results[p] = "skipped: no photo"
                log(f"{post['id']}: no photo, skipping {p}")
                continue
            text = compose(post, p, cfg)
            if dry:
                results[p] = "dry_run"
                preview = STATE / "dry_run" / f"{post['id']}.{p}.txt"
                preview.parent.mkdir(parents=True, exist_ok=True)
                preview.write_text(text, encoding="utf-8")
                log(f"[dry run] {p}: {post['id']} ({len(text)} chars)")
                continue
            if p in ("instagram", "threads"):
                # Meta flags accounts that post in quick bursts: keep at least 2 minutes between posts
                wait = META_GAP - (time.time() - LAST_META.get(p, 0))
                if wait > 0:
                    log(f"waiting {int(wait)} s before the next {p} post")
                    time.sleep(wait)
                LAST_META[p] = time.time()
            try:
                video = post.get("video_branded") or post.get("video_file")
                if video and (ROOT / video).exists() and p in VIDEO_PUBLISHERS:
                    # branded clip: Meta loads it from the repo; plain clip: from its source link
                    src_url = None if post.get("video_branded") else post.get("video_url")
                    results[p] = VIDEO_PUBLISHERS[p](text, video, src_url, cfg)
                    log(f"Published {post['id']} to {p} as video: {results[p]}")
                    continue
                slides = (post.get("carousel_files") or []) if has_photo else []
                results[p] = PUBLISHERS[p](text, post["card_file"] if has_photo else None, cfg, slides)
                log(f"Published {post['id']} to {p}: {results[p]}")
            except Exception as e:  # noqa: BLE001
                results[p] = f"ERROR: {e}"
                log(f"FAILED {post['id']} on {p}: {e}")

        entry = {
            "id": post["id"],
            "time": now_almaty().isoformat(timespec="minutes"),
            "headline": post["card"].get("headline"),
            "category": post.get("category"),
            "source_urls": post.get("source_urls", []),
            "credits": post["credits"],
            "dry_run": dry,
            "results": results,
        }
        if not dry and any(not str(r).startswith(("ERROR", "skipped")) for r in results.values()):
            remember_photos(post)
        errors = {p: r for p, r in results.items() if str(r).startswith("ERROR")}
        if errors:
            alert("❗ withBTS.kz: publishing problem.\n\n"
                  f"Post: {post['card'].get('headline', '')}\n"
                  + "\n".join(f"{p}: {r[:300]}" for p, r in errors.items())
                  + "\n\nOther platforms: " + ", ".join(p for p in results if p not in errors))
        posted = [e for e in posted if e.get("id") != post["id"]] + [entry]
        save_json(archive / f.name, post)
        f.unlink()
        if approved_run:
            for txt in pending.glob(f"{post['id']}.*.txt"):
                txt.unlink()

    save_json(STATE / "posted.json", posted[-1000:])
    if not approved_run:
        leftover = sorted(QUEUE.glob("*.json"))
        if leftover:
            alert(f"ℹ️ withBTS.kz: {len(leftover)} extra posts were over the per-run limit and were not published: "
                  + ", ".join(f.stem for f in leftover))
        for f in leftover:  # more than max_posts_per_run: set aside, never published later as stale news
            rejected.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(rejected / f.name))
            log(f"Over the per-run limit, not published: {f.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
