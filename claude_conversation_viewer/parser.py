"""JSONL parsing for Claude Code session files.

Produces three outputs from a session file:

* :func:`parse_conversation_metadata` — aggregate summary (tokens, models,
  message counts, title, cwd). Cached.
* :func:`parse_conversation_for_dashboard` — per-turn data for the 13-category
  classifier, tool breakdowns, and optimize detectors. Cached.
* :func:`parse_full_conversation` — full message list for the viewer panel.
  Not cached (read on demand).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .pricing import estimate_cost, estimate_turn_cost, get_model_pricing
from .classifier import classify_turn, PLAN_TOOLS, AGENT_TOOLS


# ---------------------------------------------------------------------------
# Metadata (existing shape — tests import via web.py re-export)
# ---------------------------------------------------------------------------


def parse_conversation_metadata(filepath: Path) -> Optional[dict]:
    session_id = filepath.stem
    project_slug = filepath.parent.name
    title = None
    preview = None
    first_timestamp = None
    last_timestamp = None
    models: set = set()
    total_input = total_output = total_cache_create = total_cache_read = 0
    user_count = assistant_count = 0
    cwd = None
    version = None

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg = obj.get("message", {})
                role = msg.get("role")
                ts = obj.get("timestamp")

                if ts:
                    if first_timestamp is None:
                        first_timestamp = ts
                    last_timestamp = ts

                if role == "user" and obj.get("type") == "user":
                    content = msg.get("content", "")
                    if isinstance(content, str) and content and not content.startswith("<"):
                        user_count += 1
                        if title is None:
                            title = content[:80]
                            preview = content[:220]
                        if cwd is None:
                            cwd = obj.get("cwd")
                        if version is None:
                            version = obj.get("version")
                    elif isinstance(content, list):
                        user_count += 1
                        if title is None:
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    text = block.get("text", "")
                                    if text and not text.startswith("<"):
                                        title = text[:80]
                                        preview = text[:220]
                                        break

                elif role == "assistant":
                    assistant_count += 1
                    model = msg.get("model")
                    if model:
                        models.add(model)
                    usage = msg.get("usage", {})
                    total_input += usage.get("input_tokens", 0)
                    total_output += usage.get("output_tokens", 0)
                    total_cache_create += usage.get("cache_creation_input_tokens", 0)
                    total_cache_read += usage.get("cache_read_input_tokens", 0)

    except (OSError, PermissionError):
        return None

    if user_count == 0 and assistant_count == 0:
        return None

    meta = {
        "id": session_id,
        "project": project_slug,
        "project_path": cwd or project_slug,
        "title": title or "(untitled)",
        "preview": preview or title or "(untitled)",
        "first_timestamp": first_timestamp,
        "last_timestamp": last_timestamp,
        "models": sorted(models),
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_cache_creation": total_cache_create,
        "total_cache_read": total_cache_read,
        "user_messages": user_count,
        "assistant_messages": assistant_count,
        "total_messages": user_count + assistant_count,
        "cwd": cwd,
        "version": version,
        "file_path": str(filepath),
    }
    meta["estimated_cost_usd"] = estimate_cost(meta)
    return meta


# ---------------------------------------------------------------------------
# Per-turn extraction for dashboard
# ---------------------------------------------------------------------------


def _extract_user_text(content) -> str:
    if isinstance(content, str):
        return content if not content.startswith("<") else ""
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text", "")
                if t and not t.startswith("<"):
                    parts.append(t)
        return " ".join(parts)
    return ""


def parse_conversation_for_dashboard(filepath: Path) -> Optional[dict]:
    """Extract per-turn tool + token data used by the Dashboard.

    Each turn is an assistant response after a user message. Consecutive
    assistant messages fold into the same turn (Claude Code streams turns
    as multiple JSONL lines when tools are used).
    """
    turns = []
    current_user_msg = ""
    current_turn = None

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts = obj.get("timestamp")
                msg = obj.get("message", {})
                role = msg.get("role")
                etype = obj.get("type")

                if role == "user" and etype == "user" and not obj.get("isMeta"):
                    content = msg.get("content", "")
                    text = _extract_user_text(content)
                    if text:
                        if current_turn:
                            turns.append(current_turn)
                        current_user_msg = text
                        current_turn = None

                elif role == "assistant":
                    if current_turn is None:
                        current_turn = {
                            "user_message": current_user_msg,
                            "timestamp": ts,
                            "model": msg.get("model"),
                            "tools": [],
                            "tool_calls": [],
                            "has_plan_mode": False,
                            "has_agent_spawn": False,
                            "usage": {
                                "input_tokens": 0,
                                "output_tokens": 0,
                                "cache_creation": 0,
                                "cache_read": 0,
                            },
                            "cost": 0.0,
                        }
                    usage = msg.get("usage", {})
                    current_turn["usage"]["input_tokens"] += usage.get("input_tokens", 0)
                    current_turn["usage"]["output_tokens"] += usage.get("output_tokens", 0)
                    current_turn["usage"]["cache_creation"] += usage.get("cache_creation_input_tokens", 0)
                    current_turn["usage"]["cache_read"] += usage.get("cache_read_input_tokens", 0)
                    if msg.get("model") and not current_turn.get("model"):
                        current_turn["model"] = msg.get("model")
                    current_turn["cost"] += estimate_turn_cost(
                        current_turn.get("model") or "",
                        {
                            "input_tokens": usage.get("input_tokens", 0),
                            "output_tokens": usage.get("output_tokens", 0),
                            "cache_creation": usage.get("cache_creation_input_tokens", 0),
                            "cache_read": usage.get("cache_read_input_tokens", 0),
                        },
                    )

                    content = msg.get("content", [])
                    if isinstance(content, list):
                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            if block.get("type") == "tool_use":
                                name = block.get("name", "unknown")
                                inp = block.get("input", {}) or {}
                                current_turn["tools"].append(name)
                                # Keep bash commands, other tools lightweight
                                compact_input = {}
                                if name in {"Bash", "BashTool", "PowerShellTool"}:
                                    cmd = inp.get("command", "")
                                    if cmd:
                                        compact_input["command"] = cmd[:200]
                                elif name == "Read":
                                    fp = inp.get("file_path", "")
                                    if fp:
                                        compact_input["file_path"] = fp
                                current_turn["tool_calls"].append({"name": name, "input": compact_input})
                                if name in PLAN_TOOLS:
                                    current_turn["has_plan_mode"] = True
                                if name in AGENT_TOOLS:
                                    current_turn["has_agent_spawn"] = True
    except (OSError, PermissionError):
        return None

    if current_turn:
        turns.append(current_turn)

    if not turns:
        return None

    # Classify every turn
    for t in turns:
        result = classify_turn(t)
        t["category"] = result["category"]
        t["retries"] = result["retries"]
        t["has_edits"] = result["has_edits"]

    return {"turns": turns}


# ---------------------------------------------------------------------------
# Full conversation for viewer (unchanged shape — tests rely on it)
# ---------------------------------------------------------------------------


def parse_full_conversation(filepath: Path) -> list:
    messages = []
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg = obj.get("message", {})
                role = msg.get("role")

                if role not in ("user", "assistant"):
                    continue
                if role == "user" and obj.get("isMeta"):
                    continue

                content = msg.get("content", "")
                if isinstance(content, str) and content.strip().startswith(("<command-name>", "<local-command")):
                    continue

                entry: dict = {
                    "role": role,
                    "timestamp": obj.get("timestamp"),
                    "uuid": obj.get("uuid"),
                }

                if role == "assistant":
                    entry["model"] = msg.get("model")
                    usage = msg.get("usage", {})
                    entry["usage"] = {
                        "input_tokens": usage.get("input_tokens", 0),
                        "output_tokens": usage.get("output_tokens", 0),
                        "cache_creation": usage.get("cache_creation_input_tokens", 0),
                        "cache_read": usage.get("cache_read_input_tokens", 0),
                    }
                    pricing = get_model_pricing(entry.get("model") or "")
                    u = entry["usage"]
                    entry["estimated_cost_usd"] = round(
                        u["input_tokens"] / 1e6 * pricing[0]
                        + u["output_tokens"] / 1e6 * pricing[1]
                        + u["cache_creation"] / 1e6 * pricing[2]
                        + u["cache_read"] / 1e6 * pricing[3],
                        6,
                    )

                if isinstance(content, str):
                    if content and not content.startswith("<"):
                        entry["content"] = [{"type": "text", "text": content}]
                    else:
                        continue
                elif isinstance(content, list):
                    blocks = []
                    for block in content:
                        if not isinstance(block, dict):
                            if isinstance(block, str) and block and not block.startswith("<"):
                                blocks.append({"type": "text", "text": block})
                            continue
                        btype = block.get("type")
                        if btype == "text":
                            text = block.get("text", "")
                            if text and not text.startswith("<system-reminder>"):
                                blocks.append({"type": "text", "text": text})
                        elif btype == "tool_use":
                            blocks.append({
                                "type": "tool_use",
                                "name": block.get("name", "unknown"),
                                "id": block.get("id", ""),
                                "input": block.get("input", {}),
                            })
                        elif btype == "tool_result":
                            rc = block.get("content", "")
                            if isinstance(rc, list):
                                rc = "\n".join(
                                    x.get("text", "") for x in rc
                                    if isinstance(x, dict) and x.get("type") == "text"
                                )
                            blocks.append({
                                "type": "tool_result",
                                "tool_use_id": block.get("tool_use_id", ""),
                                "content": str(rc)[:5000],
                            })
                        elif btype == "thinking":
                            thinking_text = block.get("thinking", "")
                            if thinking_text:
                                blocks.append({"type": "thinking", "text": thinking_text})
                    if blocks:
                        entry["content"] = blocks
                    else:
                        continue
                else:
                    continue

                messages.append(entry)

    except (OSError, PermissionError):
        pass

    return messages


def export_as_markdown(filepath: Path, metadata: dict) -> str:
    messages = parse_full_conversation(filepath)
    lines = [
        f"# {metadata.get('title', 'Conversation')}",
        "",
        f"**Session ID:** {metadata['id']}",
        f"**Project:** {metadata['project_path']}",
        f"**Date:** {metadata.get('first_timestamp', 'Unknown')}",
    ]
    if metadata.get("models"):
        lines.append(f"**Model(s):** {', '.join(metadata['models'])}")
    lines += [f"**Messages:** {metadata['total_messages']}", "", "---", ""]

    for msg in messages:
        role_label = "**User**" if msg["role"] == "user" else "**Assistant**"
        ts = msg.get("timestamp", "")
        lines.append(f"### {role_label} {('(' + ts + ')') if ts else ''}")
        lines.append("")
        for block in msg.get("content", []):
            btype = block.get("type")
            if btype == "text":
                lines.append(block["text"])
            elif btype == "tool_use":
                lines += [
                    f"<details><summary>Tool: {block['name']}</summary>",
                    "",
                    "```json",
                    json.dumps(block.get("input", {}), indent=2)[:3000],
                    "```",
                    "</details>",
                ]
            elif btype == "tool_result":
                lines += [
                    "<details><summary>Tool Result</summary>",
                    "",
                    "```",
                    str(block.get("content", ""))[:3000],
                    "```",
                    "</details>",
                ]
            lines.append("")
        lines += ["---", ""]

    return "\n".join(lines)
