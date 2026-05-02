"""Top-level /api/dashboard aggregator.

Builds the full payload consumed by the Dashboard UI:

* Overview (totals, cache hit, avg cost/session, today/month)
* Daily cost chart (date → $)
* Projects (cost, sessions, avg cost/session)
* Models (cost, tokens, calls)
* Activities (13 categories with one-shot rate)
* Core tools (Read/Edit/Bash/…)
* Shell commands (top bash command prefixes)
* MCP servers (mcp__<server>__ prefix counts)
* Top 5 most expensive sessions
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
from typing import Dict, List, Optional

from ..classifier import CATEGORIES, BASH_TOOLS, EDIT_TOOLS, READ_TOOLS
from ..pricing import clean_model_name
from .period import in_range, parse_period, ts_to_dt


CORE_TOOL_NAMES = [
    "Read", "Edit", "Write", "Bash", "Grep", "Glob",
    "TodoWrite", "Task", "WebSearch", "WebFetch", "NotebookEdit",
]


def _shell_command_head(command: str) -> str:
    """Extract the first meaningful token of a bash command for grouping."""
    if not command:
        return ""
    # Strip common prefixes like `cd foo && `, env vars, etc.
    # Simple heuristic: take the last `&&`-separated clause, then first word.
    parts = [p.strip() for p in command.split("&&")]
    last = parts[-1] if parts else command
    tokens = last.strip().split()
    if not tokens:
        return ""
    head = tokens[0]
    # Drop env-var assignments: FOO=bar cmd → cmd
    while "=" in head and len(tokens) > 1:
        tokens = tokens[1:]
        head = tokens[0]
    # For multi-word subcommands like `git status` or `npm run`, include next token
    if head in {"git", "npm", "pnpm", "yarn", "bun", "pip", "pip3", "python", "python3",
                "docker", "brew", "cargo", "go", "make", "uv", "pipx", "rustup"} and len(tokens) > 1:
        return f"{head} {tokens[1]}"
    return head


def _cache_hit_rate(total_input: int, total_cache_read: int) -> float:
    denom = total_input + total_cache_read
    if denom <= 0:
        return 0.0
    return round(total_cache_read / denom * 100, 1)


def _bucket_turns(conversations: List[dict],
                  dashboard_data: Dict[str, dict],
                  start, end) -> List[dict]:
    """Return all turns from sessions active in the range, filtered to in-range turns."""
    out = []
    for conv in conversations:
        # Skip sessions clearly outside range (cheap gate)
        last_ts = conv.get("last_timestamp")
        first_ts = conv.get("first_timestamp")
        if start and last_ts and ts_to_dt(last_ts) and ts_to_dt(last_ts) < start:
            continue
        if end and first_ts and ts_to_dt(first_ts) and ts_to_dt(first_ts) >= end:
            continue
        dash = dashboard_data.get(conv["id"], {})
        for turn in dash.get("turns", []):
            if in_range(turn.get("timestamp"), start, end):
                turn = dict(turn)
                turn["_session_id"] = conv["id"]
                turn["_project_path"] = conv.get("project_path", "")
                turn["_session_title"] = conv.get("title", "")
                out.append(turn)
    return out


def _session_summaries(conversations: List[dict],
                       dashboard_data: Dict[str, dict],
                       start, end) -> List[dict]:
    """One-row-per-session summaries for anything that touches the range."""
    out = []
    for conv in conversations:
        last_ts = conv.get("last_timestamp")
        first_ts = conv.get("first_timestamp")
        last_dt = ts_to_dt(last_ts) if last_ts else None
        first_dt = ts_to_dt(first_ts) if first_ts else None
        if start and last_dt and last_dt < start:
            continue
        if end and first_dt and first_dt >= end:
            continue

        dash = dashboard_data.get(conv["id"], {})
        turns = [t for t in dash.get("turns", []) if in_range(t.get("timestamp"), start, end)]
        if not turns and not (start is None and end is None):
            # Session exists but no turns fell in range — skip
            continue

        cost = sum(t.get("cost", 0.0) for t in turns) if turns else conv.get("estimated_cost_usd", 0.0)
        in_toks = sum(t.get("usage", {}).get("input_tokens", 0) for t in turns) if turns else conv.get("total_input_tokens", 0)
        out_toks = sum(t.get("usage", {}).get("output_tokens", 0) for t in turns) if turns else conv.get("total_output_tokens", 0)
        cache_create = sum(t.get("usage", {}).get("cache_creation", 0) for t in turns) if turns else conv.get("total_cache_creation", 0)
        cache_read = sum(t.get("usage", {}).get("cache_read", 0) for t in turns) if turns else conv.get("total_cache_read", 0)

        out.append({
            "session_id": conv["id"],
            "title": conv.get("title", ""),
            "project_path": conv.get("project_path", ""),
            "first_timestamp": first_ts,
            "last_timestamp": last_ts,
            "models": conv.get("models", []),
            "cost": round(cost, 4),
            "calls": len(turns),
            "input_tokens": in_toks,
            "output_tokens": out_toks,
            "cache_creation": cache_create,
            "cache_read": cache_read,
        })
    return out


def build_dashboard(conversations: List[dict],
                    dashboard_data: Dict[str, dict],
                    period: Optional[str] = "7d",
                    from_str: Optional[str] = None,
                    to_str: Optional[str] = None,
                    plan: Optional[dict] = None) -> dict:
    start, end = parse_period(period, from_str, to_str)

    turns = _bucket_turns(conversations, dashboard_data, start, end)
    sessions = _session_summaries(conversations, dashboard_data, start, end)

    # ── Totals ────────────────────────────────────────────────────────
    total_cost = round(sum(t.get("cost", 0.0) for t in turns), 4)
    total_calls = len(turns)
    total_input = sum(t.get("usage", {}).get("input_tokens", 0) for t in turns)
    total_output = sum(t.get("usage", {}).get("output_tokens", 0) for t in turns)
    total_cache_create = sum(t.get("usage", {}).get("cache_creation", 0) for t in turns)
    total_cache_read = sum(t.get("usage", {}).get("cache_read", 0) for t in turns)
    total_tokens = total_input + total_output + total_cache_create + total_cache_read

    total_sessions = len(sessions)
    avg_cost_session = round(total_cost / total_sessions, 4) if total_sessions else 0.0
    cache_hit = _cache_hit_rate(total_input, total_cache_read)

    # ── Today + this month (regardless of selected period) ───────────
    from .period import parse_period as _pp
    today_start, today_end = _pp("today")
    month_start, month_end = _pp("month")
    today_turns = _bucket_turns(conversations, dashboard_data, today_start, today_end)
    month_turns = _bucket_turns(conversations, dashboard_data, month_start, month_end)
    today_cost = round(sum(t.get("cost", 0.0) for t in today_turns), 4)
    month_cost = round(sum(t.get("cost", 0.0) for t in month_turns), 4)

    # ── Daily cost chart ──────────────────────────────────────────────
    daily_cost: Dict[str, float] = defaultdict(float)
    daily_calls: Dict[str, int] = defaultdict(int)
    for t in turns:
        ts = t.get("timestamp")
        if ts and len(ts) >= 10:
            day = ts[:10]
            daily_cost[day] += t.get("cost", 0.0)
            daily_calls[day] += 1
    # Fill empty days inside the range so the chart has a flat baseline
    if start and end:
        cur = start
        while cur < end:
            key = cur.strftime("%Y-%m-%d")
            if key not in daily_cost:
                daily_cost[key] = 0.0
                daily_calls[key] = 0
            cur += timedelta(days=1)
    daily = [
        {"date": day, "cost": round(cost, 4), "calls": daily_calls.get(day, 0)}
        for day, cost in sorted(daily_cost.items())
    ]

    # ── Projects ──────────────────────────────────────────────────────
    project_cost: Dict[str, float] = defaultdict(float)
    project_calls: Dict[str, int] = defaultdict(int)
    project_sessions: Dict[str, set] = defaultdict(set)
    for t in turns:
        p = t.get("_project_path") or "(unknown)"
        project_cost[p] += t.get("cost", 0.0)
        project_calls[p] += 1
        project_sessions[p].add(t.get("_session_id"))
    projects = sorted(
        (
            {
                "project": p,
                "cost": round(c, 4),
                "calls": project_calls[p],
                "sessions": len(project_sessions[p]),
                "avg_cost_per_session": round(c / len(project_sessions[p]), 4) if project_sessions[p] else 0.0,
            }
            for p, c in project_cost.items()
        ),
        key=lambda x: -x["cost"],
    )

    # ── Models ────────────────────────────────────────────────────────
    model_cost: Dict[str, float] = defaultdict(float)
    model_calls: Dict[str, int] = defaultdict(int)
    model_input: Dict[str, int] = defaultdict(int)
    model_output: Dict[str, int] = defaultdict(int)
    model_cache_r: Dict[str, int] = defaultdict(int)
    for t in turns:
        m = t.get("model") or "(unknown)"
        model_cost[m] += t.get("cost", 0.0)
        model_calls[m] += 1
        u = t.get("usage", {})
        model_input[m] += u.get("input_tokens", 0)
        model_output[m] += u.get("output_tokens", 0)
        model_cache_r[m] += u.get("cache_read", 0)
    models = sorted(
        (
            {
                "model": m,
                "label": clean_model_name(m),
                "cost": round(c, 4),
                "calls": model_calls[m],
                "input_tokens": model_input[m],
                "output_tokens": model_output[m],
                "cache_read": model_cache_r[m],
            }
            for m, c in model_cost.items()
        ),
        key=lambda x: -x["cost"],
    )

    # ── Activities (13 categories, with one-shot rate) ───────────────
    cat_cost: Dict[str, float] = defaultdict(float)
    cat_calls: Dict[str, int] = defaultdict(int)
    cat_edit_turns: Dict[str, int] = defaultdict(int)
    cat_one_shot: Dict[str, int] = defaultdict(int)
    for t in turns:
        c = t.get("category", "general")
        cat_cost[c] += t.get("cost", 0.0)
        cat_calls[c] += 1
        if t.get("has_edits"):
            cat_edit_turns[c] += 1
            if t.get("retries", 0) == 0:
                cat_one_shot[c] += 1
    activities = []
    for c in CATEGORIES:
        calls = cat_calls.get(c, 0)
        edit_turns = cat_edit_turns.get(c, 0)
        one = cat_one_shot.get(c, 0)
        activities.append({
            "category": c,
            "cost": round(cat_cost.get(c, 0.0), 4),
            "calls": calls,
            "edit_turns": edit_turns,
            "one_shot_rate": round(one / edit_turns * 100, 1) if edit_turns else None,
        })
    activities.sort(key=lambda x: -x["calls"])

    # ── Core tools ────────────────────────────────────────────────────
    core_counter: Counter = Counter()
    for t in turns:
        for name in t.get("tools", []):
            if name in CORE_TOOL_NAMES:
                core_counter[name] += 1
    core_tools = [
        {"tool": n, "count": core_counter[n]}
        for n in CORE_TOOL_NAMES
        if core_counter[n]
    ]
    core_tools.sort(key=lambda x: -x["count"])

    # ── Shell commands ────────────────────────────────────────────────
    shell_counter: Counter = Counter()
    for t in turns:
        for call in t.get("tool_calls", []):
            if call.get("name") in BASH_TOOLS:
                cmd = (call.get("input") or {}).get("command", "")
                head = _shell_command_head(cmd)
                if head:
                    shell_counter[head] += 1
    shell = [{"command": c, "count": n} for c, n in shell_counter.most_common(20)]

    # ── MCP servers ───────────────────────────────────────────────────
    mcp_counter: Counter = Counter()
    for t in turns:
        for name in t.get("tools", []):
            if name.startswith("mcp__"):
                parts = name.split("__")
                if len(parts) >= 2:
                    mcp_counter[parts[1]] += 1
    mcp = [{"server": s, "count": n} for s, n in mcp_counter.most_common(20)]

    # ── Top 5 expensive sessions ──────────────────────────────────────
    top_sessions = sorted(sessions, key=lambda s: -s["cost"])[:5]

    # ── Plan progress ─────────────────────────────────────────────────
    plan_block = None
    if plan and plan.get("preset") != "none":
        monthly_usd = float(plan.get("monthly_usd", 0.0))
        if monthly_usd > 0:
            pct = round(month_cost / monthly_usd * 100, 1)
            plan_block = {
                "preset": plan.get("preset"),
                "monthly_usd": monthly_usd,
                "month_cost": month_cost,
                "percent_used": pct,
            }

    return {
        "period": period or "7d",
        "range": {
            "from": start.isoformat() if start else None,
            "to": end.isoformat() if end else None,
        },
        "overview": {
            "cost": total_cost,
            "calls": total_calls,
            "sessions": total_sessions,
            "avg_cost_per_session": avg_cost_session,
            "cache_hit_rate_pct": cache_hit,
            "input_tokens": total_input,
            "output_tokens": total_output,
            "cache_creation_tokens": total_cache_create,
            "cache_read_tokens": total_cache_read,
            "total_tokens": total_tokens,
            "today_cost": today_cost,
            "month_cost": month_cost,
        },
        "plan": plan_block,
        "daily": daily,
        "projects": projects,
        "models": models,
        "activities": activities,
        "core_tools": core_tools,
        "shell": shell,
        "mcp": mcp,
        "top_sessions": top_sessions,
    }
