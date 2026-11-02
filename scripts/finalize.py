"""After a run: mark all candidates as seen, clear the candidate list, trim logs."""
from common import STATE, load_json, log, save_json

import os

candidates = load_json(STATE / "candidates.json", [])
seen = load_json(STATE / "seen.json", [])
if os.environ.get("WRITER_OUTCOME") == "failure":
    # Claude did not finish: keep these items unseen so the next run tries them again.
    log(f"Writer step failed, {len(candidates)} candidates kept for the next run.")
    candidates = []
known = set(seen)
seen += [c["id"] for c in candidates if c["id"] not in known]
save_json(STATE / "seen.json", seen[-3000:])
save_json(STATE / "candidates.json", [])

skipped = load_json(STATE / "skipped_log.json", [])
save_json(STATE / "skipped_log.json", skipped[-500:])
log(f"Marked {len(candidates)} candidates as seen.")

# Remove images older than 7 days so the repo stays small.
# File dates are tracked in state/media_index.json (git checkout resets file times).
import time

from common import MEDIA

index = load_json(STATE / "media_index.json", {})
now = time.time()
for p in MEDIA.glob("*"):
    if p.name == ".gitkeep":
        continue
    first_seen = index.setdefault(p.name, now)
    if now - first_seen > 7 * 24 * 3600:
        p.unlink()
        index.pop(p.name, None)
index = {k: v for k, v in index.items() if (MEDIA / k).exists()}
save_json(STATE / "media_index.json", index)
