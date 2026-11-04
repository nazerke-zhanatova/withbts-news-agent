"""Decide what Claude writes in this run (Nazerke's smart schedule), and keep it cheap.

The news check runs every 30 minutes, but Claude only writes when it is worth it:
- New items from official accounts, or from trusted fanbases that are not aggregators,
  are written right away (fast news), with the stronger model (Sonnet).
- Everything else (news sites, aggregator channels like btsnewsru) waits and is written
  in one batch every `batch_hours` hours, with the cheaper model (Haiku).

Before Claude sees anything, a free filter removes:
- items with no real text (photo-only Telegram posts),
- items that are not about BTS,
- items whose link was already used in a post,
- items whose headline repeats a story Claude already saw from 2 or more outlets in the last 3 days.
Removed items are marked as seen, so they never come back.
At most `max_batch_items` items go to Claude per run (fast items first, newest first).

Items that wait for the batch are NOT marked as seen, so the collector finds them again.
"""
import os
import re
from datetime import datetime, timedelta, timezone

from common import STATE, load_json, log, save_json, settings

BTS_WORDS = re.compile(
    r"(?<![A-Za-z])(BTS|BANGTAN|ARMY|BIGHIT|BIG HIT|HYBE|ARIRANG|WEVERSE|RM|JIN|SUGA|AGUST D|J-HOPE|JHOPE|"
    r"JIMIN|JUNGKOOK|JUNG KOOK|TAEHYUNG|NAMJOON|SEOKJIN|YOONGI|HOSEOK)(?![A-Za-z])"
    r"|방탄|비티에스|빅히트|하이브|아리랑|진|슈가|제이홉|지민|뷔|정국|"
    r"Бангтан|БТС|Чимин|Чонгук|Тэхён|Шуга|Джин|Джей-Хоуп|Намджун|Юнги|Хосок|Сокджин|АРМИ|Хайб|Бигхит",
    re.IGNORECASE,
)
STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "with", "at", "is", "as", "by", "from", "its", "bts", "s"}


def is_fast(c):
    if c.get("tier") == "official":
        return True
    return c.get("tier") == "trusted_fanbase" and c.get("role") != "aggregator"


def body(c):
    """The item's text without hashtags, emojis, links and the outlet name after ' - '."""
    t = c.get("text") or ""
    t = re.sub(r"https?://\S+|#\S+", " ", t)
    return " ".join(re.findall(r"\w+", t, flags=re.UNICODE))


def title_words(c):
    first = (c.get("text") or "").split("\n")[0]
    first = re.sub(r"\s[-|–]\s[^-|–]+$", "", first)  # drop " - Outlet name"
    return {w.lower() for w in re.findall(r"\w+", first, flags=re.UNICODE) if len(w) > 1} - STOP


def similar(a, b):
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= 0.6


def prefilter(cands, posted, recent_titles):
    used_urls = {u for p in posted for u in (p.get("source_urls") or [])}
    keep, drop = [], []
    for c in cands:
        reason = None
        words = body(c)
        short_member_post = bool(c.get("media")) and BTS_WORDS.search(c.get("text") or "")
        if c.get("platform") == "telegram" and len(words) < 25 and not short_member_post:
            reason = "no text (photo or video only)"
        elif not is_fast(c) and not BTS_WORDS.search(c.get("text") or ""):
            reason = "not about BTS"
        elif c.get("url") and c["url"] in used_urls:
            reason = "link already used in a post"
        else:
            tw = title_words(c)
            # Claude already saw this story from 2+ outlets (enough for the two-source rule)
            if not is_fast(c) and sum(similar(tw, set(r["words"])) for r in recent_titles) >= 2:
                reason = "same story already looked at"
        if reason:
            drop.append((c, reason))
        else:
            keep.append(c)
    return keep, drop


def main():
    cfg = settings()
    hours = float(cfg.get("batch_hours", 3))
    cap = int(cfg.get("max_batch_items", 15))
    now = datetime.now(timezone.utc)

    cands = load_json(STATE / "candidates.json", [])
    posted = load_json(STATE / "posted.json", [])
    titles = load_json(STATE / "seen_titles.json", [])
    titles = [t for t in titles if now - datetime.fromisoformat(t["time"]) < timedelta(days=3)]

    cands, dropped = prefilter(cands, posted, titles)
    if dropped:
        seen = load_json(STATE / "seen.json", [])
        known = set(seen)
        seen += [c["id"] for c, _ in dropped if c["id"] not in known]
        save_json(STATE / "seen.json", seen[-3000:])
        skipped = load_json(STATE / "skipped_log.json", [])
        skipped += [{"id": c["id"], "reason": f"filter: {r}", "run": now.date().isoformat()} for c, r in dropped]
        save_json(STATE / "skipped_log.json", skipped[-500:])
        log(f"Filter: removed {len(dropped)} items before Claude (" +
            ", ".join(sorted({r for _, r in dropped})) + ").")

    fast = [c for c in cands if is_fast(c)]
    rest = [c for c in cands if not is_fast(c)]
    gate = load_json(STATE / "gate.json", {})
    last = gate.get("last_batch")
    due = True
    if last:
        try:
            due = now - datetime.fromisoformat(last) >= timedelta(hours=hours)
        except ValueError:
            due = True

    if rest and due:
        rest.sort(key=lambda c: c.get("timestamp") or "", reverse=True)
        # fan channels first (few items), so news-site links never crowd them out of the batch
        rest.sort(key=lambda c: 0 if c.get("tier") == "trusted_fanbase" else 1)
        chosen = fast + rest[: max(0, cap - len(fast))]
        gate["last_batch"] = now.isoformat(timespec="minutes")
        log(f"Gate: batch time, writing {len(fast)} fast + {len(chosen) - len(fast)} other items.")
    else:
        chosen = fast[:cap]
        if rest:
            log(f"Gate: {len(rest)} other items wait for the next batch (every {hours:g} h).")
        log(f"Gate: {len(fast)} fast items (official / trusted) written now.")

    # remember the stories Claude looks at, so repeats are filtered for free next time
    titles += [{"words": sorted(title_words(c)), "time": now.isoformat(timespec="minutes")} for c in chosen]
    save_json(STATE / "seen_titles.json", titles[-2000:])
    save_json(STATE / "gate.json", gate)
    save_json(STATE / "candidates.json", chosen)

    # Sonnet for official / trusted news, Haiku (cheaper) when only news sites are in the run
    model = cfg.get("model_fast", "sonnet") if any(is_fast(c) for c in chosen) else cfg.get("model_batch", "haiku")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"count={len(chosen)}\nmodel={model}\n")
    log(f"Gate: {len(chosen)} items for Claude, model {model}.")


if __name__ == "__main__":
    main()
