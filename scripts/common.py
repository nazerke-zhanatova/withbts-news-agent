"""Shared helpers for the withBTS.kz news agent."""
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
QUEUE = ROOT / "queue"
MEDIA = ROOT / "media"
CONFIG = ROOT / "config"

ALMATY = timezone(timedelta(hours=5))


def load_yaml(name):
    with open(CONFIG / name, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def settings():
    s = load_yaml("settings.yaml")
    if not s.get("public_repo"):
        s["public_repo"] = os.environ.get("GITHUB_REPOSITORY", "")
    return s


def load_json(path, default):
    path = Path(path)
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return default


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def now_almaty():
    return datetime.now(ALMATY)


def log(msg):
    print(f"[{now_almaty():%Y-%m-%d %H:%M}] {msg}", flush=True)
