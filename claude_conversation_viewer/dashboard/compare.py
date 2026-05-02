"""Model comparison engine.

For each model in the selected period, compute:

* Performance — one-shot rate, retry rate, self-correction rate
* Efficiency — cost/call, cost/edit, output tokens/call, cache hit rate
* Behavior — delegation rate, planning rate, tools/turn, avg tools/turn

Consumed by ``GET /api/dashboard/compare``.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional

from ..classifier import AGENT_TOOLS, CATEGORIES, EDIT_TOOLS, PLAN_TOOLS, TASK_TOOLS
from ..pricing import clean_model_name
from .period import in_range, parse_period


def _pct(num: int, denom: int) -> Optional[float]:
    if denom <= 0:
        return None
    return round(num / denom * 100, 1)


def _per(num: float, denom: int) -> Optional[float]:
    if denom <= 0:
        return None
    return round(num / denom, 4)


def _compute_for_model(turns: List[dict]) -> dict:
    calls = len(turns)
    if calls == 0:
        return {}

    cost = sum(t.get("cost", 0.0) for t in turns)
    edit_turns = [t for t in turns if t.get("has_edits")]
    edit_count = len(edit_turns)
    retries_total = sum(t.get("retries", 0) for t in edit_turns)
    one_shot_count = sum(1 for t in edit_turns if t.get("retries", 0) == 0)

    # Self-correction: turn where retries > 0 but eventually ended (same turn has
    # edits). Approximation: retries > 0.
    self_corrections = sum(1 for t in edit_turns if t.get("retries", 0) > 0)

    # Efficiency
    total_input = sum(t.get("usage", {}).get("input_tokens", 0) for t in turns)
    total_output = sum(t.get("usage", {}).get("output_tokens", 0) for t in turns)
    total_cache_read = sum(t.get("usage", {}).get("cache_read", 0) for t in turns)
    cache_hit = _pct(total_cache_read, total_input + total_cache_read)

    # Behavior
    delegation_turns = sum(
        1 for t in turns
        if t.get("has_agent_spawn") or any(n in AGENT_TOOLS for n in t.get("tools", []))
    )
    plan_turns = sum(
        1 for t in turns
        if t.get("has_plan_mode") or any(n in PLAN_TOOLS for n in t.get("tools", []))
    )
    total_tools = sum(len(t.get("tools", [])) for t in turns)

    # Per-category one-shot rates
    cat_edit: Dict[str, int] = defaultdict(int)
    cat_one: Dict[str, int] = defaultdict(int)
    for t in edit_turns:
        c = t.get("category", "general")
        cat_edit[c] += 1
        if t.get("retries", 0) == 0:
            cat_one[c] += 1
    per_cat = {
        c: _pct(cat_one.get(c, 0), cat_edit.get(c, 0))
        for c in CATEGORIES
        if cat_edit.get(c, 0)
    }

    return {
        "calls": calls,
        "cost": round(cost, 4),
        "performance": {
            "one_shot_rate_pct": _pct(one_shot_count, edit_count),
            "retry_rate": _per(retries_total, edit_count),
            "self_correction_pct": _pct(self_corrections, edit_count),
        },
        "efficiency": {
            "cost_per_call": _per(cost, calls),
            "cost_per_edit": _per(cost, edit_count) if edit_count else None,
            "output_tokens_per_call": _per(total_output, calls),
            "cache_hit_rate_pct": cache_hit,
        },
        "behavior": {
            "delegation_rate_pct": _pct(delegation_turns, calls),
            "planning_rate_pct": _pct(plan_turns, calls),
            "avg_tools_per_turn": _per(total_tools, calls),
        },
        "category_one_shot_pct": per_cat,
    }


def build_compare(conversations: List[dict],
                  dashboard_data: Dict[str, dict],
                  period: Optional[str] = "30d",
                  from_str: Optional[str] = None,
                  to_str: Optional[str] = None,
                  models_filter: Optional[List[str]] = None) -> dict:
    start, end = parse_period(period, from_str, to_str)
    by_model: Dict[str, List[dict]] = defaultdict(list)
    for conv in conversations:
        dash = dashboard_data.get(conv["id"], {})
        for t in dash.get("turns", []):
            if not in_range(t.get("timestamp"), start, end):
                continue
            m = t.get("model") or "(unknown)"
            by_model[m].append(t)

    models_data = []
    for m, turns in by_model.items():
        if models_filter and m not in models_filter:
            continue
        data = _compute_for_model(turns)
        data["model"] = m
        data["label"] = clean_model_name(m)
        models_data.append(data)

    models_data.sort(key=lambda x: -x.get("calls", 0))
    return {
        "period": period or "30d",
        "range": {
            "from": start.isoformat() if start else None,
            "to": end.isoformat() if end else None,
        },
        "models": models_data,
    }
