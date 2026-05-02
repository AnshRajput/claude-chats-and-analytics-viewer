"""Waste-pattern scanner + A-F health grade.

Detects 6 high-signal waste patterns in Claude Code usage:

1. Files re-read across sessions
2. Low Read:Edit ratio
3. Cache creation overhead (system-prompt instability)
4. Junk directory reads (node_modules, .git, dist, …)
5. Wasted bash output (large / repeated)
6. Bloated CLAUDE.md

Each finding carries an impact tier (high/medium/low), estimated tokens saved,
and a ready-to-paste fix. Grade starts at 100 minus 15/7/3 per tier, capped at
an 80-point penalty, mapped to A ≥ 90, B ≥ 75, C ≥ 55, D ≥ 30, F otherwise.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional

from ..cache import get_claude_dir
from ..classifier import BASH_TOOLS, EDIT_TOOLS, READ_TOOLS
from .period import in_range, parse_period

# ---------------------------------------------------------------------------
# Thresholds (from codeburn/src/optimize.ts)
# ---------------------------------------------------------------------------

MIN_DUPLICATE_READS_TO_FLAG = 5
DUPLICATE_READS_HIGH_THRESHOLD = 30
DUPLICATE_READS_MEDIUM_THRESHOLD = 10

MIN_EDITS_FOR_RATIO = 10
HEALTHY_READ_EDIT_RATIO = 4
LOW_RATIO_HIGH_THRESHOLD = 2
LOW_RATIO_MEDIUM_THRESHOLD = 3

MIN_API_CALLS_FOR_CACHE = 10
CACHE_EXCESS_HIGH_THRESHOLD = 15_000

CLAUDEMD_HEALTHY_LINES = 200
CLAUDEMD_HIGH_THRESHOLD_LINES = 400

MIN_JUNK_READS_TO_FLAG = 3
JUNK_READS_HIGH_THRESHOLD = 20
JUNK_READS_MEDIUM_THRESHOLD = 5

BASH_OUTPUT_HIGH_CHARS = 30_000
BASH_OUTPUT_MED_CHARS = 15_000

HEALTH_WEIGHT_HIGH = 15
HEALTH_WEIGHT_MEDIUM = 7
HEALTH_WEIGHT_LOW = 3
HEALTH_MAX_PENALTY = 80

AVG_TOKENS_PER_READ = 600
CLAUDEMD_TOKENS_PER_LINE = 13
BASH_TOKENS_PER_CHAR = 0.25

JUNK_DIRS = [
    "node_modules", ".git", "dist", "build", "__pycache__", ".next",
    ".nuxt", ".output", "coverage", ".cache", ".venv", "venv",
    ".svn", ".hg",
]
JUNK_PATTERN = re.compile("/(?:" + "|".join(re.escape(d) for d in JUNK_DIRS) + ")/")


# ---------------------------------------------------------------------------
# Detector results
# ---------------------------------------------------------------------------


def _grade_from_score(score: int) -> str:
    if score >= 90:
        return "A"
    if score >= 75:
        return "B"
    if score >= 55:
        return "C"
    if score >= 30:
        return "D"
    return "F"


def _penalty(impact: str) -> int:
    return {"high": HEALTH_WEIGHT_HIGH, "medium": HEALTH_WEIGHT_MEDIUM, "low": HEALTH_WEIGHT_LOW}.get(impact, 0)


def _finding(title: str, explanation: str, impact: str,
             tokens_saved: int, fix: dict) -> dict:
    return {
        "title": title,
        "explanation": explanation,
        "impact": impact,
        "tokens_saved": int(tokens_saved),
        "fix": fix,
    }


# ---------------------------------------------------------------------------
# Detector: duplicate reads across sessions
# ---------------------------------------------------------------------------


def _detect_duplicate_reads(turns_all: List[dict]) -> Optional[dict]:
    path_reads: Counter = Counter()
    for t in turns_all:
        for call in t.get("tool_calls", []):
            if call.get("name") == "Read":
                fp = (call.get("input") or {}).get("file_path", "")
                if fp:
                    path_reads[fp] += 1

    duplicates = [(fp, n) for fp, n in path_reads.items() if n >= MIN_DUPLICATE_READS_TO_FLAG]
    if not duplicates:
        return None
    duplicates.sort(key=lambda x: -x[1])
    excess = sum(max(0, n - 1) for _, n in duplicates)

    impact = "high" if excess >= DUPLICATE_READS_HIGH_THRESHOLD else (
        "medium" if excess >= DUPLICATE_READS_MEDIUM_THRESHOLD else "low"
    )
    top = duplicates[:3]
    sample = ", ".join(f"{Path(fp).name} ×{n}" for fp, n in top)
    return _finding(
        title="Same files re-read across sessions",
        explanation=(
            f"{len(duplicates)} files were read more than {MIN_DUPLICATE_READS_TO_FLAG} times "
            f"(e.g. {sample}). Claude spends tokens re-reading the same file when "
            "CLAUDE.md could capture its contents, or when parallel sub-agents each "
            "re-read the same context."
        ),
        impact=impact,
        tokens_saved=excess * AVG_TOKENS_PER_READ,
        fix={
            "type": "paste",
            "label": "Add to CLAUDE.md",
            "text": "## Files Claude should already know\n" + "\n".join(
                f"- `{fp}` (read {n}×)" for fp, n in top
            ),
        },
    )


# ---------------------------------------------------------------------------
# Detector: Read:Edit ratio
# ---------------------------------------------------------------------------


def _detect_read_edit_ratio(turns_all: List[dict]) -> Optional[dict]:
    reads = 0
    edits = 0
    for t in turns_all:
        for name in t.get("tools", []):
            if name in READ_TOOLS:
                reads += 1
            elif name in EDIT_TOOLS:
                edits += 1
    if edits < MIN_EDITS_FOR_RATIO:
        return None
    ratio = reads / edits if edits else 0
    if ratio >= HEALTHY_READ_EDIT_RATIO:
        return None

    impact = "high" if ratio < LOW_RATIO_HIGH_THRESHOLD else (
        "medium" if ratio < LOW_RATIO_MEDIUM_THRESHOLD else "low"
    )
    # Rough savings: bringing ratio to 4:1 would add ~(4*edits - reads) reads but
    # should also prevent ~(edits * 0.3) failed retries. Estimate conservatively.
    retry_savings = int(edits * 0.3 * AVG_TOKENS_PER_READ)
    return _finding(
        title=f"Low Read:Edit ratio ({ratio:.1f}:1)",
        explanation=(
            f"{reads} reads vs {edits} edits. Editing without reading enough context "
            "leads to retries. A healthy ratio is around 4:1 for code work."
        ),
        impact=impact,
        tokens_saved=retry_savings,
        fix={
            "type": "paste",
            "label": "Add to CLAUDE.md",
            "text": (
                "## Workflow reminders\n"
                "- Read the full file before editing, not just the matched region.\n"
                "- For multi-file changes, read all affected files first."
            ),
        },
    )


# ---------------------------------------------------------------------------
# Detector: cache creation overhead
# ---------------------------------------------------------------------------


def _detect_cache_overhead(turns_all: List[dict]) -> Optional[dict]:
    calls = len(turns_all)
    if calls < MIN_API_CALLS_FOR_CACHE:
        return None
    total_create = sum(t.get("usage", {}).get("cache_creation", 0) for t in turns_all)
    total_read = sum(t.get("usage", {}).get("cache_read", 0) for t in turns_all)
    if total_read == 0 and total_create == 0:
        return None

    ratio = total_create / max(total_read, 1)
    avg_create_per_call = total_create / calls
    if avg_create_per_call <= CACHE_EXCESS_HIGH_THRESHOLD:
        return None

    impact = "high" if ratio > 0.5 else "medium"
    return _finding(
        title="Excessive cache creation",
        explanation=(
            f"Avg {int(avg_create_per_call):,} cache-creation tokens per call. "
            "System prompt or CLAUDE.md is changing session-to-session, forcing "
            "Claude to rebuild the cache every time. Stabilize system-prompt "
            "content to benefit from 10× cheaper cache reads."
        ),
        impact=impact,
        tokens_saved=int(total_create * 0.5),
        fix={
            "type": "paste",
            "label": "Stabilize CLAUDE.md",
            "text": (
                "# Review CLAUDE.md + .claude/settings.json for content that "
                "changes every session (timestamps, todos, dynamic lists). "
                "Move volatile info to external files referenced by @-imports."
            ),
        },
    )


# ---------------------------------------------------------------------------
# Detector: junk directory reads
# ---------------------------------------------------------------------------


def _detect_junk_reads(turns_all: List[dict]) -> Optional[dict]:
    junk: Counter = Counter()
    for t in turns_all:
        for call in t.get("tool_calls", []):
            if call.get("name") == "Read":
                fp = (call.get("input") or {}).get("file_path", "")
                if fp and JUNK_PATTERN.search(fp):
                    junk[fp] += 1
    total = sum(junk.values())
    if total < MIN_JUNK_READS_TO_FLAG:
        return None

    impact = "high" if total >= JUNK_READS_HIGH_THRESHOLD else (
        "medium" if total >= JUNK_READS_MEDIUM_THRESHOLD else "low"
    )
    sample = ", ".join(Path(p).name for p, _ in junk.most_common(3))
    return _finding(
        title="Reads inside build / dependency directories",
        explanation=(
            f"{total} reads fell inside directories like {sample} "
            "(node_modules, .git, dist, build, __pycache__…). These files are "
            "regenerable and rarely worth the tokens."
        ),
        impact=impact,
        tokens_saved=total * AVG_TOKENS_PER_READ,
        fix={
            "type": "paste",
            "label": "Add to CLAUDE.md",
            "text": (
                "## Directories to skip\n"
                "Never read files under: `node_modules/`, `.git/`, `dist/`, "
                "`build/`, `__pycache__/`, `.next/`, `.venv/`, `coverage/`."
            ),
        },
    )


# ---------------------------------------------------------------------------
# Detector: wasted bash output
# ---------------------------------------------------------------------------


def _detect_bash_output_waste(turns_all: List[dict]) -> Optional[dict]:
    """Flag when many bash commands appear with very long outputs.

    We don't have tool_result payloads in per-turn data, so we infer from the
    `BASH_MAX_OUTPUT_LENGTH` env var — if unset or huge, recommend capping.
    """
    import os
    current = os.environ.get("BASH_MAX_OUTPUT_LENGTH", "")
    if current:
        try:
            val = int(current)
            if val <= BASH_OUTPUT_MED_CHARS:
                return None
        except ValueError:
            pass

    bash_calls = 0
    for t in turns_all:
        for call in t.get("tool_calls", []):
            if call.get("name") in BASH_TOOLS:
                bash_calls += 1
    if bash_calls < 20:
        return None

    # Rough estimate: each uncapped bash call wastes ~2000 chars of trailing output
    saved_chars = bash_calls * 2000
    tokens_saved = int(saved_chars * BASH_TOKENS_PER_CHAR)
    impact = "medium" if bash_calls >= 100 else "low"
    return _finding(
        title="Uncapped bash output",
        explanation=(
            f"{bash_calls} bash calls observed with no BASH_MAX_OUTPUT_LENGTH "
            "cap. Claude Code defaults can let commands emit tens of thousands "
            "of characters of output that get billed as input tokens."
        ),
        impact=impact,
        tokens_saved=tokens_saved,
        fix={
            "type": "command",
            "label": "Set env var in your shell profile",
            "text": "export BASH_MAX_OUTPUT_LENGTH=15000",
        },
    )


# ---------------------------------------------------------------------------
# Detector: bloated CLAUDE.md
# ---------------------------------------------------------------------------


def _read_claudemd_lines(path: Path, depth: int = 0) -> int:
    """Count lines in a CLAUDE.md, expanding @-imports one level deep."""
    if depth > 3:
        return 0
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, PermissionError):
        return 0
    lines = text.splitlines()
    count = len(lines)
    for line in lines:
        m = re.match(r"\s*@(\S+)", line)
        if m:
            imp = m.group(1)
            imp_path = (path.parent / imp).resolve() if not imp.startswith("/") else Path(imp)
            if imp_path.exists() and imp_path != path:
                count += _read_claudemd_lines(imp_path, depth + 1)
    return count


def _detect_claudemd_bloat() -> Optional[dict]:
    candidates = [
        get_claude_dir() / "CLAUDE.md",
    ]
    results = []
    for p in candidates:
        if p.exists():
            lines = _read_claudemd_lines(p)
            if lines > CLAUDEMD_HEALTHY_LINES:
                results.append((str(p), lines))
    if not results:
        return None

    results.sort(key=lambda x: -x[1])
    worst_path, worst_lines = results[0]
    impact = "high" if worst_lines > CLAUDEMD_HIGH_THRESHOLD_LINES else "medium"
    excess = worst_lines - CLAUDEMD_HEALTHY_LINES
    return _finding(
        title=f"Bloated CLAUDE.md ({worst_lines} lines)",
        explanation=(
            f"`{worst_path}` is {worst_lines} lines (with @-import expansion). "
            f"Every session pays for this in the system prompt. Healthy target "
            f"is ≤{CLAUDEMD_HEALTHY_LINES} lines."
        ),
        impact=impact,
        tokens_saved=excess * CLAUDEMD_TOKENS_PER_LINE,
        fix={
            "type": "paste",
            "label": "Trim your CLAUDE.md",
            "text": (
                "# Move the least-referenced sections into dedicated files and "
                "@-import only the ones you need:\n"
                "#   @./docs/deploy.md\n"
                "#   @./docs/testing.md\n"
                "# Keep top-level CLAUDE.md to high-signal conventions and "
                "non-obvious invariants."
            ),
        },
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_optimize(conversations: List[dict],
                   dashboard_data: Dict[str, dict],
                   period: Optional[str] = "30d",
                   from_str: Optional[str] = None,
                   to_str: Optional[str] = None) -> dict:
    start, end = parse_period(period, from_str, to_str)
    turns_all: List[dict] = []
    for conv in conversations:
        dash = dashboard_data.get(conv["id"], {})
        for t in dash.get("turns", []):
            if in_range(t.get("timestamp"), start, end):
                turns_all.append(t)

    findings = []
    for detector in (
        _detect_duplicate_reads,
        _detect_read_edit_ratio,
        _detect_cache_overhead,
        _detect_junk_reads,
        _detect_bash_output_waste,
    ):
        result = detector(turns_all)
        if result:
            findings.append(result)

    cmd_result = _detect_claudemd_bloat()
    if cmd_result:
        findings.append(cmd_result)

    # Rank by impact then tokens saved
    impact_order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (impact_order.get(f["impact"], 3), -f["tokens_saved"]))

    # Health grade
    penalty = sum(_penalty(f["impact"]) for f in findings)
    penalty = min(penalty, HEALTH_MAX_PENALTY)
    score = 100 - penalty
    grade = _grade_from_score(score)

    return {
        "period": period or "30d",
        "range": {
            "from": start.isoformat() if start else None,
            "to": end.isoformat() if end else None,
        },
        "findings": findings,
        "score": score,
        "grade": grade,
        "turns_scanned": len(turns_all),
    }
