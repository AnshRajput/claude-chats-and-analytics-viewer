"""Git-correlated yield tracking.

For each session, find commits authored in the session's time window inside
its `cwd`, then classify the outcome:

* **productive** — commits landed in HEAD and are not reverted
* **reverted** — a later commit reverts one of them
* **abandoned** — no commits inside the window, or commits exist on a branch
  that never reached HEAD
"""

from __future__ import annotations

import os
import subprocess
from datetime import timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .period import in_range, parse_period, ts_to_dt


def _git(cwd: Path, args: List[str], timeout: int = 5) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
        if result.returncode != 0:
            return None
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _is_git_repo(cwd: Path) -> bool:
    out = _git(cwd, ["rev-parse", "--is-inside-work-tree"])
    return bool(out and out.strip() == "true")


def _commits_in_range(cwd: Path, since_iso: str, until_iso: str) -> List[Tuple[str, str]]:
    """Return list of (sha, subject) committed between since/until (HEAD only)."""
    out = _git(cwd, [
        "log",
        "--since", since_iso,
        "--until", until_iso,
        "--pretty=format:%H%x09%s",
        "HEAD",
    ])
    if not out:
        return []
    pairs = []
    for line in out.splitlines():
        if "\t" in line:
            sha, subject = line.split("\t", 1)
            pairs.append((sha, subject))
    return pairs


def _reverts_in_repo(cwd: Path, since_iso: str) -> List[str]:
    """SHAs that appear in revert commit subjects since ``since_iso``."""
    out = _git(cwd, [
        "log",
        "--since", since_iso,
        "--pretty=format:%s",
        "HEAD",
    ])
    if not out:
        return []
    reverted = []
    for line in out.splitlines():
        # `git revert` subject: "Revert \"original subject\"\n\n This reverts commit <sha>."
        # We rely on commit body too — fetch each revert with --format
        pass
    # Pull bodies too
    out2 = _git(cwd, [
        "log",
        "--since", since_iso,
        "--grep=^Revert",
        "--pretty=format:%H%x09%B%x1e",
        "HEAD",
    ])
    if not out2:
        return []
    for entry in out2.split("\x1e"):
        for token in entry.split():
            # reverts mention "This reverts commit <sha>."
            if len(token) == 41 and token.endswith("."):
                reverted.append(token[:40])
            elif len(token) == 40 and all(c in "0123456789abcdef" for c in token):
                reverted.append(token)
    return reverted


def _classify_session(cwd: Path, session_start: str, session_end: str) -> dict:
    if not _is_git_repo(cwd):
        return {"status": "no-git", "commits": [], "reverted": 0}
    # Expand the window by 2h to catch commits made just after the session
    start_dt = ts_to_dt(session_start)
    end_dt = ts_to_dt(session_end)
    if not start_dt or not end_dt:
        return {"status": "unknown", "commits": [], "reverted": 0}
    since = (start_dt - timedelta(hours=1)).isoformat()
    until = (end_dt + timedelta(hours=2)).isoformat()
    commits = _commits_in_range(cwd, since, until)
    if not commits:
        return {"status": "abandoned", "commits": [], "reverted": 0}

    revert_since = (start_dt - timedelta(hours=1)).isoformat()
    reverted_shas = set(_reverts_in_repo(cwd, revert_since))
    reverted_count = sum(1 for sha, _ in commits if sha in reverted_shas or sha[:12] in reverted_shas)

    status = "reverted" if reverted_count > 0 else "productive"
    return {
        "status": status,
        "commits": [{"sha": sha[:10], "subject": subj} for sha, subj in commits],
        "reverted": reverted_count,
    }


def build_yield(conversations: List[dict],
                dashboard_data: Dict[str, dict],
                period: Optional[str] = "7d",
                from_str: Optional[str] = None,
                to_str: Optional[str] = None,
                project_filter: Optional[str] = None) -> dict:
    start, end = parse_period(period, from_str, to_str)
    by_status = {"productive": 0, "reverted": 0, "abandoned": 0, "no-git": 0, "unknown": 0}
    cost_by_status = {"productive": 0.0, "reverted": 0.0, "abandoned": 0.0, "no-git": 0.0, "unknown": 0.0}
    sessions: List[dict] = []

    for conv in conversations:
        if start and conv.get("last_timestamp") and ts_to_dt(conv["last_timestamp"]) and ts_to_dt(conv["last_timestamp"]) < start:
            continue
        if end and conv.get("first_timestamp") and ts_to_dt(conv["first_timestamp"]) and ts_to_dt(conv["first_timestamp"]) >= end:
            continue
        cwd = conv.get("cwd") or conv.get("project_path")
        if not cwd or not Path(cwd).exists():
            continue
        if project_filter and project_filter.lower() not in (cwd or "").lower():
            continue
        dash = dashboard_data.get(conv["id"], {})
        turns = [t for t in dash.get("turns", []) if in_range(t.get("timestamp"), start, end)]
        if not turns:
            continue
        cost = sum(t.get("cost", 0.0) for t in turns)
        result = _classify_session(
            Path(cwd),
            turns[0].get("timestamp", conv.get("first_timestamp", "")),
            turns[-1].get("timestamp", conv.get("last_timestamp", "")),
        )
        status = result["status"]
        by_status[status] = by_status.get(status, 0) + 1
        cost_by_status[status] = cost_by_status.get(status, 0.0) + cost
        sessions.append({
            "session_id": conv["id"],
            "title": conv.get("title", ""),
            "project_path": cwd,
            "cost": round(cost, 4),
            "status": status,
            "commits": result["commits"][:10],
            "reverted_count": result["reverted"],
        })

    sessions.sort(key=lambda s: -s["cost"])
    total = sum(by_status.values())
    breakdown = {
        k: {
            "sessions": by_status[k],
            "cost": round(cost_by_status[k], 4),
            "percent": round(by_status[k] / total * 100, 1) if total else 0.0,
        }
        for k in ("productive", "reverted", "abandoned", "no-git", "unknown")
    }

    return {
        "period": period or "7d",
        "range": {
            "from": start.isoformat() if start else None,
            "to": end.isoformat() if end else None,
        },
        "breakdown": breakdown,
        "sessions": sessions[:100],
        "total_sessions": total,
    }
