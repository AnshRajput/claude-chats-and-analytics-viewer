"""
Update checker for Claude Code Conversation Viewer.
Compares installed version against the latest on PyPI.
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.request import urlopen
from urllib.error import URLError

from claude_conversation_viewer import __version__

PACKAGE_NAME = "claude-conversation-viewer"
PYPI_URL = f"https://pypi.org/pypi/{PACKAGE_NAME}/json"
CACHE_FILE = Path(tempfile.gettempdir()) / "claude-viewer-update-check"
CHECK_INTERVAL = 3600  # 1 hour


def _parse_version(v: str) -> tuple:
    """Parse version string into comparable tuple."""
    try:
        return tuple(int(x) for x in v.strip().split("."))
    except (ValueError, AttributeError):
        return (0,)


def _read_cache() -> Optional[dict]:
    try:
        data = json.loads(CACHE_FILE.read_text())
        if time.time() - data["timestamp"] < CHECK_INTERVAL:
            return data
    except Exception:
        pass
    return None


def _write_cache(update_available: bool, current: str, latest: str):
    try:
        CACHE_FILE.write_text(json.dumps({
            "timestamp": time.time(),
            "update_available": update_available,
            "current_version": current,
            "latest_version": latest,
        }))
    except Exception:
        pass


def _do_check() -> dict:
    cached = _read_cache()
    if cached is not None:
        return cached

    current = __version__

    try:
        resp = urlopen(PYPI_URL, timeout=10)
        data = json.loads(resp.read().decode("utf-8"))
        latest = data["info"]["version"]
    except Exception:
        return {"update_available": False, "current_version": current}

    available = _parse_version(latest) > _parse_version(current)
    result = {
        "update_available": available,
        "current_version": current,
        "latest_version": latest,
    }
    _write_cache(available, current, latest)
    return result


def check_for_update_sync() -> dict:
    return _do_check()


def check_for_update_async(callback: Callable[[bool], None]):
    def _run():
        result = _do_check()
        callback(result.get("update_available", False))

    t = threading.Thread(target=_run, daemon=True)
    t.start()
