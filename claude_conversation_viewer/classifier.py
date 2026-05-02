"""Deterministic 13-category task classifier.

Ported from getagentseal/codeburn's src/classifier.ts. No LLM calls; pure
regex + tool-set matching so results are fast, private, and reproducible.
"""

from __future__ import annotations

import re
from typing import List, Optional, Set

# ---------------------------------------------------------------------------
# Category labels
# ---------------------------------------------------------------------------

CATEGORIES = [
    "coding",
    "debugging",
    "feature",
    "refactoring",
    "testing",
    "exploration",
    "planning",
    "delegation",
    "git",
    "build/deploy",
    "brainstorming",
    "conversation",
    "general",
]

# ---------------------------------------------------------------------------
# Bash-command regex matchers
# ---------------------------------------------------------------------------

TEST_PATTERNS = re.compile(
    r"\b(test|pytest|vitest|jest|mocha|spec|coverage|npm\s+test|npx\s+vitest|npx\s+jest)\b",
    re.I,
)
GIT_PATTERNS = re.compile(
    r"\bgit\s+(push|pull|commit|merge|rebase|checkout|branch|stash|log|diff|status|add|reset|cherry-pick|tag)\b",
    re.I,
)
BUILD_PATTERNS = re.compile(
    r"\b(npm\s+run\s+build|npm\s+publish|pip\s+install|docker|deploy|make\s+build|npm\s+run\s+dev|npm\s+start|pm2|systemctl|brew|cargo\s+build)\b",
    re.I,
)
INSTALL_PATTERNS = re.compile(
    r"\b(npm\s+install|pip\s+install|brew\s+install|apt\s+install|cargo\s+add)\b", re.I,
)

# ---------------------------------------------------------------------------
# User-message keyword matchers
# ---------------------------------------------------------------------------

DEBUG_KEYWORDS = re.compile(
    r"\b(fix|bug|error|broken|failing|crash|issue|debug|traceback|exception|stack\s*trace|not\s+working|wrong|unexpected|status\s+code|404|500|401|403)\b",
    re.I,
)
FEATURE_KEYWORDS = re.compile(
    r"\b(add|create|implement|new|build|feature|introduce|set\s*up|scaffold|generate|make\s+(?:a|me|the)|write\s+(?:a|me|the))\b",
    re.I,
)
REFACTOR_KEYWORDS = re.compile(
    r"\b(refactor|clean\s*up|rename|reorganize|simplify|extract|restructure|move|migrate|split)\b",
    re.I,
)
BRAINSTORM_KEYWORDS = re.compile(
    r"\b(brainstorm|idea|what\s+if|explore|think\s+about|approach|strategy|design|consider|how\s+should|what\s+would|opinion|suggest|recommend)\b",
    re.I,
)
RESEARCH_KEYWORDS = re.compile(
    r"\b(research|investigate|look\s+into|find\s+out|check|search|analyze|review|understand|explain|how\s+does|what\s+is|show\s+me|list|compare)\b",
    re.I,
)

FILE_PATTERNS = re.compile(
    r"\.(py|js|ts|tsx|jsx|json|yaml|yml|toml|sql|sh|go|rs|java|rb|php|css|html|md|csv|xml)\b",
    re.I,
)
SCRIPT_PATTERNS = re.compile(
    r"\b(run\s+\S+\.\w+|execute|scrip?t|curl|api\s+\S+|endpoint|request\s+url|fetch\s+\S+|query|database|db\s+\S+)\b",
    re.I,
)
URL_PATTERN = re.compile(r"https?:\/\/\S+", re.I)

# ---------------------------------------------------------------------------
# Tool-name sets
# ---------------------------------------------------------------------------

EDIT_TOOLS: Set[str] = {"Edit", "Write", "NotebookEdit", "FileEditTool", "FileWriteTool"}
READ_TOOLS: Set[str] = {"Read", "Grep", "Glob", "FileReadTool", "GrepTool", "GlobTool"}
BASH_TOOLS: Set[str] = {"Bash", "BashTool", "PowerShellTool"}
TASK_TOOLS: Set[str] = {"TaskCreate", "TaskUpdate", "TaskGet", "TaskList", "TaskOutput", "TaskStop", "TodoWrite"}
SEARCH_TOOLS: Set[str] = {"WebSearch", "WebFetch"}
PLAN_TOOLS: Set[str] = {"EnterPlanMode", "ExitPlanMode"}
AGENT_TOOLS: Set[str] = {"Agent", "Task", "dispatch_agent"}
SKILL_TOOLS: Set[str] = {"Skill"}


# ---------------------------------------------------------------------------
# Turn shape (duck-typed dict):
#   {
#     "user_message": str,
#     "tools": [str, ...],                   # all tool names used in turn
#     "tool_calls": [{"name", "input"}, ...] # ordered; input may have .command for Bash
#     "has_plan_mode": bool,
#     "has_agent_spawn": bool,
#   }
# ---------------------------------------------------------------------------


def _has_any(tools: List[str], pool: Set[str]) -> bool:
    return any(t in pool for t in tools)


def _has_mcp(tools: List[str]) -> bool:
    return any(t.startswith("mcp__") for t in tools)


def _classify_by_tool_pattern(turn: dict) -> Optional[str]:
    tools = turn.get("tools", [])
    if not tools:
        return None

    if turn.get("has_plan_mode"):
        return "planning"
    if turn.get("has_agent_spawn"):
        return "delegation"
    if _has_any(tools, AGENT_TOOLS):
        return "delegation"

    has_edits = _has_any(tools, EDIT_TOOLS)
    has_reads = _has_any(tools, READ_TOOLS)
    has_bash = _has_any(tools, BASH_TOOLS)
    has_tasks = _has_any(tools, TASK_TOOLS)
    has_search = _has_any(tools, SEARCH_TOOLS)
    has_mcp = _has_mcp(tools)
    has_skill = _has_any(tools, SKILL_TOOLS)

    if has_bash and not has_edits:
        msg = turn.get("user_message", "") or ""
        if TEST_PATTERNS.search(msg):
            return "testing"
        if GIT_PATTERNS.search(msg):
            return "git"
        if BUILD_PATTERNS.search(msg):
            return "build/deploy"
        if INSTALL_PATTERNS.search(msg):
            return "build/deploy"

    if has_edits:
        return "coding"
    if has_bash and has_reads:
        return "exploration"
    if has_bash:
        return "coding"
    if has_search or has_mcp:
        return "exploration"
    if has_reads and not has_edits:
        return "exploration"
    if has_tasks and not has_edits:
        return "planning"
    if has_skill:
        return "general"
    return None


def _refine_by_keywords(category: str, user_message: str) -> str:
    msg = user_message or ""
    if category == "coding":
        if DEBUG_KEYWORDS.search(msg):
            return "debugging"
        if REFACTOR_KEYWORDS.search(msg):
            return "refactoring"
        if FEATURE_KEYWORDS.search(msg):
            return "feature"
        return "coding"
    if category == "exploration":
        if RESEARCH_KEYWORDS.search(msg):
            return "exploration"
        if DEBUG_KEYWORDS.search(msg):
            return "debugging"
        return "exploration"
    return category


def _classify_conversation(user_message: str) -> str:
    msg = user_message or ""
    if BRAINSTORM_KEYWORDS.search(msg):
        return "brainstorming"
    if RESEARCH_KEYWORDS.search(msg):
        return "exploration"
    if DEBUG_KEYWORDS.search(msg):
        return "debugging"
    if FEATURE_KEYWORDS.search(msg):
        return "feature"
    if FILE_PATTERNS.search(msg):
        return "coding"
    if SCRIPT_PATTERNS.search(msg):
        return "coding"
    if URL_PATTERN.search(msg):
        return "exploration"
    return "conversation"


def count_retries(tool_calls: List[dict]) -> int:
    """Edit → Bash → Edit patterns count as retries."""
    saw_edit = False
    saw_bash_after_edit = False
    retries = 0
    for call in tool_calls:
        name = call.get("name", "")
        if name in EDIT_TOOLS:
            if saw_bash_after_edit:
                retries += 1
            saw_edit = True
            saw_bash_after_edit = False
        elif name in BASH_TOOLS and saw_edit:
            saw_bash_after_edit = True
    return retries


def turn_has_edits(tool_calls: List[dict]) -> bool:
    return any(c.get("name") in EDIT_TOOLS for c in tool_calls)


def classify_turn(turn: dict) -> dict:
    """Return {category, retries, has_edits} for a turn dict."""
    tools = turn.get("tools", [])
    tool_calls = turn.get("tool_calls", [])
    msg = turn.get("user_message", "") or ""

    if not tools:
        category = _classify_conversation(msg)
    else:
        tool_cat = _classify_by_tool_pattern(turn)
        category = _refine_by_keywords(tool_cat, msg) if tool_cat else _classify_conversation(msg)

    return {
        "category": category,
        "retries": count_retries(tool_calls),
        "has_edits": turn_has_edits(tool_calls),
    }
