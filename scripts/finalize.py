"""After a run: mark all candidates as seen, clear the candidate list, trim logs."""
from common import STATE, alert, load_json, log, save_json

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

# Mistake notes from the writer (CLAUDE.md rule 18): send new lines to Nazerke.
notes_file = STATE / "notes_for_nazerke.md"
if notes_file.exists():
    lines = [l for l in notes_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    sent = load_json(STATE / "notes_sent.json", {"count": 0})
    new_lines = lines[sent.get("count", 0):]
    if new_lines:
        alert("📝 withBTS.kz: a note from the agent (a possible mistake, or news it could not post).\n\n" + "\n".join(new_lines)[:3500])
        save_json(STATE / "notes_sent.json", {"count": len(lines)})

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
