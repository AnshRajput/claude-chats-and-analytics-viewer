"""Filesystem cache for parsed session metadata + per-turn dashboard data.

Cache version 3 adds per-turn data alongside existing metadata. Bumping the
version discards the old cache cleanly — callers just re-parse on first load.
"""

from __future__ import annotations

import json
import os
import platform
import tempfile
from pathlib import Path

CACHE_VERSION = 3


def get_claude_dir() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("USERPROFILE", Path.home()))
    else:
        base = Path.home()
    return base / ".claude"


def get_projects_dir() -> Path:
    return get_claude_dir() / "projects"


def get_cache_path() -> Path:
    return Path(tempfile.gettempdir()) / f"claude-viewer-cache-v{CACHE_VERSION}.json"


def get_bookmarks_path() -> Path:
    return get_claude_dir() / "viewer-bookmarks.json"


def get_plan_path() -> Path:
    return get_claude_dir() / "viewer-plan.json"


# ---------------------------------------------------------------------------
# Metadata cache
# ---------------------------------------------------------------------------


def load_metadata_cache() -> dict:
    try:
        p = get_cache_path()
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("version") == CACHE_VERSION:
                return data.get("entries", {})
    except Exception:
        pass
    return {}


def save_metadata_cache(entries: dict):
    try:
        get_cache_path().write_text(
            json.dumps({"version": CACHE_VERSION, "entries": entries}),
            encoding="utf-8",
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Bookmarks
# ---------------------------------------------------------------------------


def load_bookmarks() -> dict:
    try:
        p = get_bookmarks_path()
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            return data.get("bookmarks", {})
    except Exception:
        pass
    return {}


def save_bookmarks(bookmarks: dict):
    try:
        p = get_bookmarks_path()
        p.write_text(
            json.dumps({"version": 1, "bookmarks": bookmarks}, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Subscription plan config
# ---------------------------------------------------------------------------


def load_plan() -> dict:
    try:
        p = get_plan_path()
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"preset": "none", "monthly_usd": 0.0}


def save_plan(plan: dict):
    try:
        get_plan_path().write_text(json.dumps(plan, indent=2), encoding="utf-8")
    except Exception:
        pass
