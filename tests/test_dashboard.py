"""Tests for the dashboard data layer — classifier, aggregator, optimize."""

from __future__ import annotations

from datetime import datetime, timezone

from claude_conversation_viewer.classifier import classify_turn, CATEGORIES
from claude_conversation_viewer.dashboard.aggregator import build_dashboard
from claude_conversation_viewer.dashboard.optimize import build_optimize
from claude_conversation_viewer.dashboard.compare import build_compare
from claude_conversation_viewer.dashboard.period import parse_period, in_range
from claude_conversation_viewer.dashboard.plans import normalize_plan, PRESETS


# ── Classifier ────────────────────────────────────────────────────────────────


def _turn(user_msg="", tools=(), tool_calls=None, **extra):
    base = {
        "user_message": user_msg,
        "tools": list(tools),
        "tool_calls": tool_calls or [{"name": t, "input": {}} for t in tools],
        "has_plan_mode": False,
        "has_agent_spawn": False,
        "model": "claude-sonnet-4-6",
        "timestamp": "2026-05-02T10:00:00Z",
        "usage": {"input_tokens": 100, "output_tokens": 50, "cache_creation": 0, "cache_read": 0},
        "cost": 0.01,
    }
    base.update(extra)
    return base


def test_classifier_13_categories_exist():
    expected = {"coding", "debugging", "feature", "refactoring", "testing",
                "exploration", "planning", "delegation", "git", "build/deploy",
                "brainstorming", "conversation", "general"}
    assert expected == set(CATEGORIES)


def test_classify_edit_tools_is_coding():
    turn = _turn(user_msg="update the styles file", tools=["Edit"])
    result = classify_turn(turn)
    assert result["category"] in {"coding", "feature", "refactoring"}


def test_classify_debug_keyword_routes_to_debugging():
    turn = _turn(user_msg="fix the broken login bug", tools=["Edit", "Bash"])
    result = classify_turn(turn)
    assert result["category"] == "debugging"


def test_classify_refactor_keyword_routes_to_refactoring():
    turn = _turn(user_msg="refactor the pricing module to split concerns", tools=["Edit"])
    assert classify_turn(turn)["category"] == "refactoring"


def test_classify_feature_keyword_routes_to_feature():
    turn = _turn(user_msg="add a new export button to the dashboard", tools=["Edit", "Write"])
    assert classify_turn(turn)["category"] == "feature"


def test_classify_bash_with_pytest_is_testing():
    turn = _turn(
        user_msg="run pytest on the new module",
        tools=["Bash"],
        tool_calls=[{"name": "Bash", "input": {"command": "pytest tests/"}}],
    )
    assert classify_turn(turn)["category"] == "testing"


def test_classify_bash_with_git_is_git():
    turn = _turn(
        user_msg="git commit and push",
        tools=["Bash"],
        tool_calls=[{"name": "Bash", "input": {"command": "git push origin main"}}],
    )
    assert classify_turn(turn)["category"] == "git"


def test_classify_plan_mode_is_planning():
    turn = _turn(user_msg="let's plan this", tools=["EnterPlanMode"], has_plan_mode=True)
    assert classify_turn(turn)["category"] == "planning"


def test_classify_agent_spawn_is_delegation():
    turn = _turn(user_msg="run explorer", tools=["Agent"], has_agent_spawn=True)
    assert classify_turn(turn)["category"] == "delegation"


def test_classify_no_tools_brainstorm_keyword():
    turn = _turn(user_msg="what if we redesigned the onboarding? brainstorm with me")
    assert classify_turn(turn)["category"] == "brainstorming"


def test_classify_no_tools_conversation_fallback():
    turn = _turn(user_msg="thanks that's great")
    assert classify_turn(turn)["category"] == "conversation"


def test_classify_retries_counted_on_edit_bash_edit():
    tool_calls = [
        {"name": "Edit", "input": {}},
        {"name": "Bash", "input": {}},
        {"name": "Edit", "input": {}},  # retry
        {"name": "Bash", "input": {}},
        {"name": "Edit", "input": {}},  # retry
    ]
    turn = _turn(user_msg="fix the bug", tools=["Edit", "Bash"], tool_calls=tool_calls)
    result = classify_turn(turn)
    assert result["retries"] == 2
    assert result["has_edits"] is True


def test_classify_one_shot_has_zero_retries():
    turn = _turn(
        user_msg="add a log line",
        tools=["Edit"],
        tool_calls=[{"name": "Edit", "input": {}}],
    )
    assert classify_turn(turn)["retries"] == 0


# ── Period parsing ────────────────────────────────────────────────────────────


def test_parse_period_all_is_unbounded():
    start, end = parse_period("all")
    assert start is None and end is None


def test_parse_period_today_window():
    start, end = parse_period("today")
    assert start is not None and end is not None
    assert (end - start).days == 1


def test_parse_period_7d_window():
    start, end = parse_period("7d")
    assert (end - start).days == 7


def test_parse_period_month_starts_first_of_month():
    start, end = parse_period("month")
    assert start.day == 1


def test_parse_period_custom_explicit_range():
    start, end = parse_period("custom", "2026-01-01", "2026-01-10")
    assert start.year == 2026 and start.month == 1 and start.day == 1
    assert end.year == 2026 and end.month == 1 and end.day == 11


def test_in_range_respects_bounds():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 10, tzinfo=timezone.utc)
    assert in_range("2026-01-05T00:00:00Z", start, end)
    assert not in_range("2026-01-10T00:00:01Z", start, end)
    assert not in_range("2025-12-31T00:00:00Z", start, end)


# ── Aggregator ────────────────────────────────────────────────────────────────


def test_aggregator_empty_returns_zeros():
    payload = build_dashboard([], {}, period="7d")
    assert payload["overview"]["cost"] == 0
    assert payload["overview"]["calls"] == 0
    assert payload["overview"]["sessions"] == 0
    assert payload["activities"]  # still lists all 13 categories (with zero counts)


def test_aggregator_core_tools_counted():
    conv = {"id": "s1", "project_path": "/p", "first_timestamp": "2026-05-02T10:00:00Z",
            "last_timestamp": "2026-05-02T10:30:00Z", "estimated_cost_usd": 0.0,
            "total_input_tokens": 0, "total_output_tokens": 0,
            "total_cache_creation": 0, "total_cache_read": 0, "title": "t", "models": []}
    dash = {
        "s1": {
            "turns": [
                _turn(tools=["Edit", "Bash", "Read"], user_message="implement foo"),
                _turn(tools=["Read", "Grep"], user_message="look for pattern"),
            ]
        }
    }
    payload = build_dashboard([conv], dash, period="7d")
    core_counts = {c["tool"]: c["count"] for c in payload["core_tools"]}
    assert core_counts.get("Edit", 0) == 1
    assert core_counts.get("Read", 0) == 2
    assert core_counts.get("Grep", 0) == 1


def test_aggregator_shell_command_groups_git_subcommands():
    conv = {"id": "s1", "project_path": "/p", "first_timestamp": "2026-05-02T10:00:00Z",
            "last_timestamp": "2026-05-02T10:30:00Z", "estimated_cost_usd": 0.0,
            "total_input_tokens": 0, "total_output_tokens": 0,
            "total_cache_creation": 0, "total_cache_read": 0, "title": "t", "models": []}
    dash = {
        "s1": {
            "turns": [
                _turn(tools=["Bash"], tool_calls=[{"name": "Bash", "input": {"command": "git status"}}]),
                _turn(tools=["Bash"], tool_calls=[{"name": "Bash", "input": {"command": "git diff HEAD"}}]),
                _turn(tools=["Bash"], tool_calls=[{"name": "Bash", "input": {"command": "ls -la"}}]),
            ]
        }
    }
    payload = build_dashboard([conv], dash, period="7d")
    shell = {s["command"]: s["count"] for s in payload["shell"]}
    assert shell.get("git status") == 1
    assert shell.get("git diff") == 1
    assert shell.get("ls") == 1


# ── Optimize + grading ────────────────────────────────────────────────────────


def test_optimize_empty_gives_grade_a():
    payload = build_optimize([], {}, period="7d")
    assert payload["grade"] == "A"
    assert payload["score"] == 100
    assert payload["findings"] == []


def test_optimize_grade_bands():
    from claude_conversation_viewer.dashboard.optimize import _grade_from_score
    assert _grade_from_score(100) == "A"
    assert _grade_from_score(90) == "A"
    assert _grade_from_score(89) == "B"
    assert _grade_from_score(75) == "B"
    assert _grade_from_score(56) == "C"
    assert _grade_from_score(35) == "D"
    assert _grade_from_score(29) == "F"


# ── Compare ───────────────────────────────────────────────────────────────────


def test_compare_empty_returns_empty_models():
    payload = build_compare([], {}, period="7d")
    assert payload["models"] == []


# ── Plans ─────────────────────────────────────────────────────────────────────


def test_plans_preset_claude_max():
    plan = normalize_plan("claude-max")
    assert plan["monthly_usd"] == 200.0


def test_plans_custom_uses_supplied_amount():
    plan = normalize_plan("custom", monthly_usd=42.0)
    assert plan["monthly_usd"] == 42.0
    assert plan["preset"] == "custom"


def test_plans_unknown_falls_back_to_none():
    plan = normalize_plan("not-a-real-preset")
    assert plan["preset"] == "none"
    assert plan["monthly_usd"] == 0.0
