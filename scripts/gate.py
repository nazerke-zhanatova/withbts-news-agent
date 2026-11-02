"""Decide what Claude writes in this run (Nazerke's smart schedule).

The news check runs every 30 minutes, but Claude only writes when it is worth it:
- New items from official accounts, or from trusted fanbases that are not aggregators,
  are written right away (fast news).
- Everything else (news sites, aggregator channels like btsnewsru) waits and is written
  in one batch every `batch_hours` hours (default 3).

Items that wait are removed from state/candidates.json but NOT marked as seen,
so the collector finds them again and they are written in the next batch.
"""
from datetime import datetime, timedelta, timezone

from common import STATE, load_json, log, save_json, settings


def is_fast(c):
    if c.get("tier") == "official":
        return True
    return c.get("tier") == "trusted_fanbase" and c.get("role") != "aggregator"


def main():
    cfg = settings()
    hours = float(cfg.get("batch_hours", 3))
    cands = load_json(STATE / "candidates.json", [])
    fast = [c for c in cands if is_fast(c)]
    rest = [c for c in cands if not is_fast(c)]

    gate = load_json(STATE / "gate.json", {})
    now = datetime.now(timezone.utc)
    last = gate.get("last_batch")
    due = True
    if last:
        try:
            due = now - datetime.fromisoformat(last) >= timedelta(hours=hours)
        except ValueError:
            due = True

    if rest and due:
        chosen = fast + rest
        gate["last_batch"] = now.isoformat(timespec="minutes")
        log(f"Gate: batch time, writing {len(fast)} fast + {len(rest)} other items.")
    else:
        chosen = fast
        if rest:
            log(f"Gate: {len(rest)} other items wait for the next batch (every {hours:g} h).")
        log(f"Gate: {len(fast)} fast items (official / trusted) written now.")
    save_json(STATE / "gate.json", gate)
    save_json(STATE / "candidates.json", chosen)
    print(len(chosen))


if __name__ == "__main__":
    main()
