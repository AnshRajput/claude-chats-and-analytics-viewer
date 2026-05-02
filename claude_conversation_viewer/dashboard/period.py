"""Date range parsing for dashboard period filters."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

# ISO 8601 with optional trailing Z — Claude Code timestamps end with Z.


def _parse_iso(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def parse_period(
    period: Optional[str],
    from_str: Optional[str] = None,
    to_str: Optional[str] = None,
) -> Tuple[Optional[datetime], Optional[datetime]]:
    """Return (start, end) tuple. ``None`` for either means unbounded.

    Supported values for ``period``:
        ``today`` | ``7d`` | ``week`` | ``30d`` | ``month`` | ``all`` | ``custom``

    When ``period == "custom"`` the caller-supplied ``from_str``/``to_str``
    (YYYY-MM-DD) are honored.
    """
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    key = (period or "7d").lower()

    if key in ("all", ""):
        return None, None

    if key == "today":
        return today_start, today_start + timedelta(days=1)

    if key in ("week", "7d"):
        return today_start - timedelta(days=6), today_start + timedelta(days=1)

    if key == "30d":
        return today_start - timedelta(days=29), today_start + timedelta(days=1)

    if key == "month":
        start = today_start.replace(day=1)
        return start, today_start + timedelta(days=1)

    if key == "custom":
        def _parse_bound(s, is_end):
            if not s:
                return None
            # YYYY-MM-DD → midnight UTC (for end, bump to next midnight so date is inclusive)
            if len(s) == 10 and s.count("-") == 2:
                try:
                    d = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    return d + timedelta(days=1) if is_end else d
                except ValueError:
                    return None
            return _parse_iso(s)

        start = _parse_bound(from_str, False)
        end = _parse_bound(to_str, True)
        return start, end

    # Unknown → default to 7 days
    return today_start - timedelta(days=6), today_start + timedelta(days=1)


def in_range(ts: Optional[str], start: Optional[datetime], end: Optional[datetime]) -> bool:
    if not ts:
        return start is None
    dt = _parse_iso(ts)
    if dt is None:
        return False
    if start and dt < start:
        return False
    if end and dt >= end:
        return False
    return True


def ts_to_dt(ts: Optional[str]) -> Optional[datetime]:
    return _parse_iso(ts) if ts else None
