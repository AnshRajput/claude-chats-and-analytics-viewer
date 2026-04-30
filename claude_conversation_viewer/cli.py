#!/usr/bin/env python3
"""
Claude Code Conversation Viewer - CLI
======================================
Browse, search, and resume your Claude Code conversations from the terminal.

Usage:
    python3 claude_conversations_cli.py                  # interactive list
    python3 claude_conversations_cli.py --search "auth"  # search conversations
    python3 claude_conversations_cli.py --project "rent"  # filter by project
    python3 claude_conversations_cli.py --view <id>      # view a conversation
    python3 claude_conversations_cli.py --resume <id>    # resume in Claude Code

Requirements: Python 3.7+ (no pip install needed)
Works on: macOS, Windows, Linux
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

try:
    from claude_conversation_viewer.update_checker import check_for_update_async
except ImportError:
    check_for_update_async = None

_update_available = False

# ---------------------------------------------------------------------------
# Terminal colors (works on macOS, Linux, Windows 10+)
# ---------------------------------------------------------------------------

class C:
    """ANSI color codes. Auto-disabled if not a TTY."""
    _enabled = sys.stdout.isatty()

    RESET     = "\033[0m"  if _enabled else ""
    BOLD      = "\033[1m"  if _enabled else ""
    DIM       = "\033[2m"  if _enabled else ""
    ITALIC    = "\033[3m"  if _enabled else ""
    UNDERLINE = "\033[4m"  if _enabled else ""
    # Standard colors
    RED       = "\033[31m" if _enabled else ""
    GREEN     = "\033[32m" if _enabled else ""
    YELLOW    = "\033[33m" if _enabled else ""
    BLUE      = "\033[34m" if _enabled else ""
    PURPLE    = "\033[35m" if _enabled else ""
    CYAN      = "\033[36m" if _enabled else ""
    WHITE     = "\033[37m" if _enabled else ""
    # 256-color for richer visuals
    ORANGE    = "\033[38;5;208m" if _enabled else ""
    PINK      = "\033[38;5;176m" if _enabled else ""
    GRAY      = "\033[38;5;245m" if _enabled else ""
    LIGHT_GRAY = "\033[38;5;250m" if _enabled else ""
    DARK_GRAY = "\033[38;5;238m" if _enabled else ""
    SKY       = "\033[38;5;111m" if _enabled else ""
    LIME      = "\033[38;5;149m" if _enabled else ""
    LAVENDER  = "\033[38;5;141m" if _enabled else ""
    GOLD      = "\033[38;5;220m" if _enabled else ""
    # Backgrounds
    BG_DARK   = "\033[48;5;236m" if _enabled else ""
    BG_ROW    = "\033[48;5;234m" if _enabled else ""
    BG_PURPLE = "\033[48;5;53m"  if _enabled else ""
    BG_BLUE   = "\033[48;5;17m"  if _enabled else ""


# Box drawing characters
class Box:
    H  = "─"   # horizontal
    V  = "│"   # vertical
    TL = "╭"   # top-left
    TR = "╮"   # top-right
    BL = "╰"   # bottom-left
    BR = "╯"   # bottom-right
    T  = "┬"   # top tee
    B  = "┴"   # bottom tee
    L  = "├"   # left tee
    R  = "┤"   # right tee
    X  = "┼"   # cross
    DH = "═"   # double horizontal
    DV = "║"   # double vertical

# ---------------------------------------------------------------------------
# Path detection
# ---------------------------------------------------------------------------

def get_claude_dir() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("USERPROFILE", Path.home()))
    else:
        base = Path.home()
    return base / ".claude"


def get_projects_dir() -> Path:
    return get_claude_dir() / "projects"


def shorten_path(full_path: str) -> str:
    if not full_path:
        return ""
    parts = full_path.replace("\\", "/").split("/")
    # Find the home dir boundary
    if "Users" in parts:
        idx = parts.index("Users")
        home = parts[idx + 2:]  # skip Users/<username>
    elif "home" in parts:
        idx = parts.index("home")
        home = parts[idx + 2:]
    else:
        home = parts
    home = [p for p in home if p]
    if not home:
        return "~"
    if len(home) <= 2:
        return "~/" + "/".join(home)
    return "/".join(home[-2:])

# ---------------------------------------------------------------------------
# JSONL parser
# ---------------------------------------------------------------------------

def parse_conversation_metadata(filepath: Path) -> Optional[dict]:
    session_id = filepath.stem
    project_slug = filepath.parent.name
    title = None
    first_timestamp = None
    last_timestamp = None
    models = set()
    total_input_tokens = 0
    total_output_tokens = 0
    user_msg_count = 0
    assistant_msg_count = 0
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
                        user_msg_count += 1
                        if title is None:
                            title = content[:120]
                        if cwd is None:
                            cwd = obj.get("cwd")
                        if version is None:
                            version = obj.get("version")
                    elif isinstance(content, str) and content.startswith("<"):
                        pass
                    else:
                        user_msg_count += 1

                elif role == "assistant":
                    assistant_msg_count += 1
                    model = msg.get("model")
                    if model:
                        models.add(model)
                    usage = msg.get("usage", {})
                    total_input_tokens += usage.get("input_tokens", 0)
                    total_output_tokens += usage.get("output_tokens", 0)

    except (OSError, PermissionError):
        return None

    if user_msg_count == 0 and assistant_msg_count == 0:
        return None

    return {
        "id": session_id,
        "project": project_slug,
        "project_path": cwd or project_slug,
        "title": title or "(no title)",
        "first_timestamp": first_timestamp,
        "last_timestamp": last_timestamp,
        "models": sorted(models),
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "user_messages": user_msg_count,
        "assistant_messages": assistant_msg_count,
        "total_messages": user_msg_count + assistant_msg_count,
        "cwd": cwd,
        "version": version,
        "file_path": str(filepath),
    }


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

                if isinstance(content, str) and content.strip().startswith("<command-name>"):
                    continue
                if isinstance(content, str) and content.strip().startswith("<local-command"):
                    continue

                entry = {
                    "role": role,
                    "timestamp": obj.get("timestamp"),
                }

                if role == "assistant":
                    entry["model"] = msg.get("model")

                if isinstance(content, str):
                    if content and not content.startswith("<"):
                        entry["content"] = [{"type": "text", "text": content}]
                    else:
                        continue
                elif isinstance(content, list):
                    blocks = []
                    for block in content:
                        if isinstance(block, dict):
                            btype = block.get("type")
                            if btype == "text":
                                text = block.get("text", "")
                                if text and not text.startswith("<system-reminder>"):
                                    blocks.append({"type": "text", "text": text})
                            elif btype == "tool_use":
                                blocks.append({
                                    "type": "tool_use",
                                    "name": block.get("name", "unknown"),
                                    "input": block.get("input", {}),
                                })
                            elif btype == "tool_result":
                                result_content = block.get("content", "")
                                if isinstance(result_content, list):
                                    texts = []
                                    for rc in result_content:
                                        if isinstance(rc, dict) and rc.get("type") == "text":
                                            texts.append(rc.get("text", ""))
                                    result_content = "\n".join(texts)
                                blocks.append({
                                    "type": "tool_result",
                                    "content": str(result_content)[:2000],
                                })
                        elif isinstance(block, str):
                            if block and not block.startswith("<"):
                                blocks.append({"type": "text", "text": block})
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

# ---------------------------------------------------------------------------
# Load all conversations
# ---------------------------------------------------------------------------

def load_all_conversations() -> list:
    projects_dir = get_projects_dir()
    if not projects_dir.exists():
        print(f"{C.RED}Error:{C.RESET} Claude projects directory not found: {projects_dir}")
        sys.exit(1)

    conversations = []
    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue
        for jsonl_file in sorted(project_dir.glob("*.jsonl")):
            meta = parse_conversation_metadata(jsonl_file)
            if meta:
                conversations.append(meta)

    conversations.sort(key=lambda c: c.get("last_timestamp") or "", reverse=True)
    return conversations

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def format_date(ts: str) -> str:
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        now = datetime.now(dt.tzinfo)
        diff = now - dt
        days = diff.days
        if days == 0:
            return "Today " + dt.strftime("%H:%M")
        if days == 1:
            return "Yesterday"
        if days < 7:
            return dt.strftime("%a")
        return dt.strftime("%b %d, %Y")
    except Exception:
        return ts[:10]


def format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def get_terminal_width() -> int:
    return shutil.get_terminal_size((80, 24)).columns

# ---------------------------------------------------------------------------
# Display: conversation list
# ---------------------------------------------------------------------------

def print_welcome(total: int):
    """Print a welcome banner on first launch."""
    tw = get_terminal_width()
    bw = min(56, tw - 4)  # box width

    print()
    # Top border
    print(f"  {C.LAVENDER}{Box.TL}{Box.H * bw}{Box.TR}{C.RESET}")
    # Title
    title = " Claude Code Conversations "
    pad = bw - len(title)
    lp = pad // 2
    rp = pad - lp
    print(f"  {C.LAVENDER}{Box.V}{C.RESET}{C.BOLD}{C.LAVENDER}{' ' * lp}{title}{' ' * rp}{C.RESET}{C.LAVENDER}{Box.V}{C.RESET}")
    # Subtitle
    sub = f" {total} conversations found "
    pad2 = bw - len(sub)
    lp2 = pad2 // 2
    rp2 = pad2 - lp2
    print(f"  {C.LAVENDER}{Box.V}{C.RESET}{C.GRAY}{' ' * lp2}{sub}{' ' * rp2}{C.RESET}{C.LAVENDER}{Box.V}{C.RESET}")
    # Divider
    print(f"  {C.LAVENDER}{Box.L}{Box.H * bw}{Box.R}{C.RESET}")
    # Commands
    cmds = [
        (f"{C.GOLD}1{C.RESET}{C.GRAY}-{C.RESET}{C.GOLD}20{C.RESET}",       "View conversation details",  10),
        (f"{C.GOLD}v 5{C.RESET}",        "Read full conversation",     10),
        (f"{C.GOLD}r 5{C.RESET}",        "Resume in Claude Code",      10),
        (f"{C.GOLD}s keyword{C.RESET}",  "Search conversations",       14),
        (f"{C.GOLD}n{C.RESET} {C.GRAY}/{C.RESET} {C.GOLD}p{C.RESET}",       "Next / previous page",       10),
        (f"{C.GOLD}h{C.RESET}",          "Help",                       10),
        (f"{C.GOLD}q{C.RESET}",          "Quit",                       10),
    ]
    for cmd_str, desc, visible_len in cmds:
        # visible_len is the visible character count of cmd_str (without ANSI)
        gap = 16 - visible_len
        line = f"    {cmd_str}{' ' * gap}{C.GRAY}{desc}{C.RESET}"
        print(f"  {C.LAVENDER}{Box.V}{C.RESET}{line}{' ' * (bw - 4 - 16 - len(desc))}{C.LAVENDER}{Box.V}{C.RESET}")
    # Bottom border
    print(f"  {C.LAVENDER}{Box.BL}{Box.H * bw}{Box.BR}{C.RESET}")
    print()


def print_conversation_list(conversations: list, page: int = 0, page_size: int = 20):
    total = len(conversations)
    start = page * page_size
    end = min(start + page_size, total)
    page_convs = conversations[start:end]
    total_pages = (total + page_size - 1) // page_size
    tw = get_terminal_width()

    # Page indicator
    page_dots = ""
    for i in range(total_pages):
        if i == page:
            page_dots += f"{C.LAVENDER}●{C.RESET} "
        else:
            page_dots += f"{C.DARK_GRAY}○{C.RESET} "

    print()
    print(f"  {page_dots} {C.GRAY}Page {page + 1}/{total_pages}  {C.DARK_GRAY}({total} conversations){C.RESET}")
    print()

    # Column widths
    num_w = 3
    date_w = 12
    msgs_w = 5
    tok_w = 7
    proj_w = 20
    # Title + ID get the rest, stacked in each row
    fixed_w = num_w + date_w + msgs_w + tok_w + proj_w + 14
    title_w = max(20, tw - fixed_w)

    # Header
    hdr = (
        f"  {C.DARK_GRAY}"
        f"{'#':>{num_w}} {Box.V} "
        f"{'Date':<{date_w}} "
        f"{'Msgs':>{msgs_w}} "
        f"{'Tokens':>{tok_w}}  "
        f"{'Project':<{proj_w}} "
        f"{'Conversation':<{title_w}}"
        f"{C.RESET}"
    )
    print(hdr)
    table_w = min(tw - 2, num_w + date_w + msgs_w + tok_w + proj_w + title_w + 14)
    print(f"  {C.DARK_GRAY}{Box.H * table_w}{C.RESET}")

    for i, c in enumerate(page_convs):
        num = start + i + 1
        date = format_date(c.get("first_timestamp", ""))
        msgs = str(c["total_messages"])
        tokens = format_tokens(c["total_input_tokens"] + c["total_output_tokens"])
        proj = shorten_path(c.get("project_path", ""))
        if len(proj) > proj_w:
            proj = proj[:proj_w - 2] + ".."
        title = c["title"]
        if len(title) > title_w:
            title = title[:title_w - 2] + ".."
        sid_short = c["id"][:8]

        # Alternate row background feel via color intensity
        if i % 2 == 0:
            num_c = C.GOLD
            date_c = C.LIGHT_GRAY
            msg_c = C.WHITE
            tok_c = C.YELLOW
            proj_c = C.LIME
            title_c = C.WHITE
            id_c = C.DARK_GRAY
        else:
            num_c = C.GOLD
            date_c = C.GRAY
            msg_c = C.GRAY
            tok_c = C.GOLD + C.DIM
            proj_c = C.LIME + C.DIM
            title_c = C.GRAY
            id_c = C.DARK_GRAY

        # Main row: number | date  msgs  tokens  project  title
        print(
            f"  {num_c}{num:>{num_w}}{C.RESET} {C.DARK_GRAY}{Box.V}{C.RESET} "
            f"{date_c}{date:<{date_w}}{C.RESET} "
            f"{msg_c}{msgs:>{msgs_w}}{C.RESET} "
            f"{tok_c}{tokens:>{tok_w}}{C.RESET}  "
            f"{proj_c}{proj:<{proj_w}}{C.RESET} "
            f"{title_c}{title}{C.RESET}"
        )
        # Sub-row: session ID
        pad = num_w + 2  # align with after the │
        print(
            f"  {' ' * num_w} {C.DARK_GRAY}{Box.V}{C.RESET} "
            f"{id_c}{sid_short}{C.RESET}"
        )

    print(f"  {C.DARK_GRAY}{Box.H * table_w}{C.RESET}")
    print()
    return page_convs


def print_conversation_detail(conv: dict):
    """Print full conversation ID and metadata in a styled card."""
    tw = get_terminal_width()
    bw = min(76, tw - 4)
    inner = bw - 2

    tokens = conv["total_input_tokens"] + conv["total_output_tokens"]
    title = conv["title"]
    if len(title) > inner - 4:
        title = title[:inner - 6] + ".."

    print()
    # Top
    print(f"  {C.LAVENDER}{Box.TL}{Box.H * bw}{Box.TR}{C.RESET}")

    # Title
    print(f"  {C.LAVENDER}{Box.V}{C.RESET} {C.BOLD}{C.WHITE}{title}{C.RESET}{' ' * (inner - len(title) - 1)}{C.LAVENDER}{Box.V}{C.RESET}")

    # Divider
    print(f"  {C.LAVENDER}{Box.L}{Box.H * bw}{Box.R}{C.RESET}")

    # Info rows
    def info_row(label, value, val_color=C.WHITE):
        lbl = f"{C.GRAY}{label:<14}{C.RESET}"
        val = f"{val_color}{value}{C.RESET}"
        # Calculate visible length of value for padding
        visible_val_len = len(value)
        pad = inner - 14 - visible_val_len - 2
        if pad < 0:
            pad = 0
        print(f"  {C.LAVENDER}{Box.V}{C.RESET} {lbl}{val}{' ' * pad}{C.LAVENDER}{Box.V}{C.RESET}")

    info_row("Session ID", conv["id"], C.SKY)
    info_row("Project", conv.get("project_path", ""), C.LIME)
    info_row("Date", format_date(conv.get("first_timestamp", "")), C.LIGHT_GRAY)
    info_row("Messages", f"{conv['total_messages']} ({conv['user_messages']} user, {conv['assistant_messages']} assistant)", C.WHITE)
    if conv.get("models"):
        info_row("Model(s)", ", ".join(conv["models"]), C.LAVENDER)
    info_row("Tokens", f"{format_tokens(tokens)} (in: {format_tokens(conv['total_input_tokens'])}, out: {format_tokens(conv['total_output_tokens'])})", C.GOLD)

    # Resume command section
    print(f"  {C.LAVENDER}{Box.L}{Box.H * bw}{Box.R}{C.RESET}")
    resume_cmd = f"claude --resume {conv['id']}"
    lbl = f"{C.GRAY}{'Resume':<14}{C.RESET}"
    val = f"{C.BOLD}{C.LAVENDER}{resume_cmd}{C.RESET}"
    pad = inner - 14 - len(resume_cmd) - 2
    if pad < 0:
        pad = 0
    print(f"  {C.LAVENDER}{Box.V}{C.RESET} {lbl}{val}{' ' * pad}{C.LAVENDER}{Box.V}{C.RESET}")

    # Bottom
    print(f"  {C.LAVENDER}{Box.BL}{Box.H * bw}{Box.BR}{C.RESET}")
    print()


def print_conversation_messages(filepath: Path):
    """Print the full conversation messages with styled formatting."""
    messages = parse_full_conversation(filepath)
    tw = get_terminal_width()
    text_width = min(100, tw - 8)
    msg_count = len(messages)

    print(f"  {C.GRAY}{msg_count} messages{C.RESET}")
    print()

    for idx, msg in enumerate(messages):
        role = msg["role"]
        ts = ""
        if msg.get("timestamp"):
            try:
                dt = datetime.fromisoformat(msg["timestamp"].replace("Z", "+00:00"))
                ts = dt.strftime("%H:%M:%S")
            except Exception:
                ts = ""

        # Message header with role badge
        if role == "user":
            badge = f"{C.BOLD}{C.SKY} USER {C.RESET}"
            border_c = C.SKY
            prefix = f"{C.DARK_GRAY}{Box.V}{C.RESET}"
        else:
            model = msg.get("model", "")
            model_tag = f"  {C.DARK_GRAY}{model}{C.RESET}" if model else ""
            badge = f"{C.BOLD}{C.LAVENDER} ASSISTANT {C.RESET}{model_tag}"
            border_c = C.LAVENDER
            prefix = f"{C.DARK_GRAY}{Box.V}{C.RESET}"

        # Header line
        ts_str = f"  {C.DARK_GRAY}{ts}{C.RESET}" if ts else ""
        print(f"  {border_c}{Box.TL}{Box.H * 2}{C.RESET} {badge}{ts_str}")

        for block in msg.get("content", []):
            btype = block.get("type")
            if btype == "text":
                text = block["text"]
                for line in text.split("\n"):
                    if len(line) <= text_width:
                        print(f"  {prefix}  {line}")
                    else:
                        for wrapped in textwrap.wrap(line, width=text_width):
                            print(f"  {prefix}  {wrapped}")
            elif btype == "tool_use":
                name = block.get("name", "?")
                inp = block.get("input", {})
                summary = ""
                if name == "Bash" and inp.get("command"):
                    summary = inp["command"][:80]
                elif name in ("Read", "Write", "Edit") and inp.get("file_path"):
                    summary = inp["file_path"]
                elif name == "Grep" and inp.get("pattern"):
                    summary = inp["pattern"]
                elif name == "Glob" and inp.get("pattern"):
                    summary = inp["pattern"]
                else:
                    summary = str(inp)[:80]
                print(f"  {prefix}  {C.ORANGE}{Box.TL}{Box.H} {name}{C.RESET} {C.GRAY}{summary}{C.RESET}")
            elif btype == "tool_result":
                content = block.get("content", "")
                preview = content[:120].replace("\n", " ")
                print(f"  {prefix}  {C.GREEN}{Box.BL}{Box.H} Result:{C.RESET} {C.DARK_GRAY}{preview}{C.RESET}")

        # Footer line
        print(f"  {border_c}{Box.BL}{Box.H * 2}{C.RESET}")
        print()

# ---------------------------------------------------------------------------
# Interactive mode
# ---------------------------------------------------------------------------

def print_help():
    """Print help for interactive commands."""
    tw = get_terminal_width()
    bw = min(62, tw - 4)

    print()
    print(f"  {C.LAVENDER}{Box.TL}{Box.H * bw}{Box.TR}{C.RESET}")
    title = " Help "
    pad = bw - len(title)
    print(f"  {C.LAVENDER}{Box.V}{' ' * (pad // 2)}{C.BOLD}{title}{C.RESET}{' ' * (pad - pad // 2)}{C.LAVENDER}{Box.V}{C.RESET}")
    print(f"  {C.LAVENDER}{Box.L}{Box.H * bw}{Box.R}{C.RESET}")

    sections = [
        ("Browsing", [
            ("{C.GOLD}3{C.RESET}",            "Show details for conversation #3"),
            ("{C.GOLD}v 3{C.RESET}",          "Read full conversation #3"),
            ("{C.GOLD}n{C.RESET} / {C.GOLD}p{C.RESET}",          "Next / previous page"),
        ]),
        ("Searching", [
            ("{C.GOLD}s flutter{C.RESET}",    "Search all conversations for 'flutter'"),
            ("{C.GOLD}a{C.RESET}",            "Clear search, show all"),
        ]),
        ("Resuming", [
            ("{C.GOLD}r 3{C.RESET}",          "Resume conversation #3 in Claude Code"),
            ("{C.GOLD}r 4925f6c7{C.RESET}",   "Resume by session ID (partial IDs work)"),
        ]),
        ("Other", [
            ("{C.GOLD}h{C.RESET}",            "Show this help"),
            ("{C.GOLD}q{C.RESET}",            "Quit"),
        ]),
    ]

    for sec_name, cmds in sections:
        print(f"  {C.LAVENDER}{Box.V}{C.RESET}  {C.BOLD}{C.WHITE}{sec_name}{C.RESET}{' ' * (bw - len(sec_name) - 3)}{C.LAVENDER}{Box.V}{C.RESET}")
        for cmd_tpl, desc in cmds:
            cmd_str = cmd_tpl.format(C=C)
            # Approximate visible length
            clean = cmd_tpl.replace("{C.GOLD}", "").replace("{C.RESET}", "").replace("{C.GRAY}", "")
            gap = 18 - len(clean)
            if gap < 1:
                gap = 1
            line_visible = 4 + len(clean) + gap + len(desc)
            rpad = bw - line_visible - 1
            if rpad < 0:
                rpad = 0
            print(f"  {C.LAVENDER}{Box.V}{C.RESET}    {cmd_str}{' ' * gap}{C.GRAY}{desc}{C.RESET}{' ' * rpad}{C.LAVENDER}{Box.V}{C.RESET}")
        print(f"  {C.LAVENDER}{Box.V}{' ' * bw}{Box.V}{C.RESET}")

    print(f"  {C.LAVENDER}{Box.BL}{Box.H * bw}{Box.BR}{C.RESET}")
    print()


def interactive_mode(conversations: list, is_search: bool = False):
    all_conversations = conversations
    page = 0
    page_size = 20
    total_pages = max(1, (len(conversations) + page_size - 1) // page_size)
    first_run = True

    while True:
        if first_run and not is_search:
            print_welcome(len(conversations))
            if _update_available:
                print(f"  {C.LAVENDER}  ✦ Update available! Run: {C.GOLD}pip install --upgrade claude-conversation-viewer{C.RESET}")
                print()
            first_run = False

        if is_search and first_run:
            print(f"\n  {C.GREEN}Found {len(conversations)} matching conversations{C.RESET}")
            first_run = False

        displayed = print_conversation_list(conversations, page, page_size)

        # Compact hint line
        print(f"  {C.DARK_GRAY}Enter a number to view {Box.V} {C.GOLD}v #{C.RESET}{C.DARK_GRAY} read {Box.V} {C.GOLD}r #{C.RESET}{C.DARK_GRAY} resume {Box.V} {C.GOLD}s{C.RESET}{C.DARK_GRAY} search {Box.V} {C.GOLD}h{C.RESET}{C.DARK_GRAY} help{C.RESET}")
        print()

        try:
            if is_search:
                prompt = f"  {C.ORANGE}search {C.LAVENDER}❯{C.RESET} "
            else:
                prompt = f"  {C.LAVENDER}❯{C.RESET} "
            raw = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not raw:
            continue

        cmd = raw.lower()

        if cmd in ("q", "quit", "exit"):
            break

        elif cmd in ("h", "help", "?"):
            print_help()
            input(f"  {C.DIM}Press Enter to continue...{C.RESET}")

        elif cmd in ("a", "all", "clear"):
            if is_search:
                return  # exit back to parent interactive_mode with all conversations
            else:
                page = 0

        elif cmd == "n":
            if page < total_pages - 1:
                page += 1
            else:
                print(f"\n  {C.DIM}Already on the last page.{C.RESET}")
                input(f"  {C.DIM}Press Enter to continue...{C.RESET}")

        elif cmd == "p":
            if page > 0:
                page -= 1
            else:
                print(f"\n  {C.DIM}Already on the first page.{C.RESET}")
                input(f"  {C.DIM}Press Enter to continue...{C.RESET}")

        elif cmd.startswith("s "):
            query = raw[2:].strip().lower()
            if query:
                filtered = [
                    c for c in all_conversations
                    if query in (c.get("title") or "").lower()
                    or query in (c.get("project_path") or "").lower()
                    or any(query in m.lower() for m in c.get("models", []))
                    or query in c.get("id", "").lower()
                ]
                if filtered:
                    interactive_mode(filtered, is_search=True)
                    # After returning from search, re-show current list
                    continue
                else:
                    print(f"\n  {C.YELLOW}No conversations matching \"{raw[2:].strip()}\"{C.RESET}")
                    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")
            continue

        elif cmd.startswith("r "):
            # Resume
            target = raw[2:].strip()
            conv = _resolve_conversation(target, conversations, page, page_size)
            if conv:
                print_conversation_detail(conv)
                try:
                    confirm = input(f"  {C.BOLD}Resume this conversation?{C.RESET} {C.DIM}(y/n){C.RESET} ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    print()
                    continue
                if confirm in ("y", "yes"):
                    print(f"\n  {C.PURPLE}Launching:{C.RESET} claude --resume {conv['id']}\n")
                    os.execvp("claude", ["claude", "--resume", conv["id"]])
                else:
                    print(f"  {C.DIM}Cancelled.{C.RESET}")
            else:
                print(f"\n  {C.RED}Not found.{C.RESET} Use a row number (e.g. {C.BOLD}r 3{C.RESET}) or session ID (e.g. {C.BOLD}r 4925f6c7{C.RESET})")
                input(f"  {C.DIM}Press Enter to continue...{C.RESET}")

        elif cmd.startswith("v "):
            # View full messages
            target = raw[2:].strip()
            conv = _resolve_conversation(target, conversations, page, page_size)
            if conv:
                print_conversation_detail(conv)
                filepath = Path(conv["file_path"])
                print_conversation_messages(filepath)

                print(f"  {C.DIM}{'-' * 40}{C.RESET}")
                try:
                    action = input(f"  {C.DIM}Press Enter to go back, or type{C.RESET} {C.BOLD}r{C.RESET} {C.DIM}to resume this conversation:{C.RESET} ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    print()
                    continue
                if action in ("r", "resume"):
                    print(f"\n  {C.PURPLE}Launching:{C.RESET} claude --resume {conv['id']}\n")
                    os.execvp("claude", ["claude", "--resume", conv["id"]])
            else:
                print(f"\n  {C.RED}Not found.{C.RESET} Use a row number like {C.BOLD}v 3{C.RESET}")
                input(f"  {C.DIM}Press Enter to continue...{C.RESET}")

        else:
            # Try as a number to view details
            conv = _resolve_conversation(raw, conversations, page, page_size)
            if conv:
                print_conversation_detail(conv)
                try:
                    action = input(f"  {C.DIM}Type{C.RESET} {C.BOLD}v{C.RESET} {C.DIM}to read full messages,{C.RESET} {C.BOLD}r{C.RESET} {C.DIM}to resume, or Enter to go back:{C.RESET} ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    print()
                    continue
                if action in ("v", "view"):
                    filepath = Path(conv["file_path"])
                    print_conversation_messages(filepath)
                    print(f"  {C.DIM}{'-' * 40}{C.RESET}")
                    try:
                        action2 = input(f"  {C.DIM}Press Enter to go back, or type{C.RESET} {C.BOLD}r{C.RESET} {C.DIM}to resume:{C.RESET} ").strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        print()
                        continue
                    if action2 in ("r", "resume"):
                        print(f"\n  {C.PURPLE}Launching:{C.RESET} claude --resume {conv['id']}\n")
                        os.execvp("claude", ["claude", "--resume", conv["id"]])
                elif action in ("r", "resume"):
                    print(f"\n  {C.PURPLE}Launching:{C.RESET} claude --resume {conv['id']}\n")
                    os.execvp("claude", ["claude", "--resume", conv["id"]])
            else:
                print(f"\n  {C.RED}Unknown command: {raw}{C.RESET}  {C.DIM}(type {C.BOLD}h{C.RESET}{C.DIM} for help){C.RESET}")
                input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


def _resolve_conversation(target: str, conversations: list, page: int, page_size: int):
    """Resolve a target (row number or session ID prefix) to a conversation."""
    # Try as row number first
    try:
        num = int(target)
        idx = page * page_size + num - 1
        if 0 <= idx < len(conversations):
            return conversations[idx]
    except ValueError:
        pass

    # Try as session ID prefix
    return next((c for c in conversations if c["id"] == target or c["id"].startswith(target)), None)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Claude Code Conversation Viewer - CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              %(prog)s                          Interactive browser
              %(prog)s --search "auth"          Search conversations
              %(prog)s --project "rentok"       Filter by project name
              %(prog)s --view <session-id>      View a conversation
              %(prog)s --resume <session-id>    Resume in Claude Code
              %(prog)s --list                   Non-interactive list
        """),
    )
    parser.add_argument("--list", action="store_true", help="List all conversations (non-interactive)")
    parser.add_argument("--search", type=str, help="Search conversations by keyword")
    parser.add_argument("--project", type=str, help="Filter by project name")
    parser.add_argument("--view", type=str, metavar="SESSION_ID", help="View a conversation by session ID")
    parser.add_argument("--resume", type=str, metavar="SESSION_ID", help="Resume a conversation in Claude Code")
    parser.add_argument("--limit", type=int, default=50, help="Max conversations to show in --list mode (default: 50)")
    args = parser.parse_args()

    # Start background update check
    def _on_update_result(available):
        global _update_available
        _update_available = available

    if check_for_update_async is not None:
        check_for_update_async(_on_update_result)

    conversations = load_all_conversations()

    if not conversations:
        print(f"{C.RED}No conversations found.{C.RESET}")
        print(f"Expected conversation files in: {get_projects_dir()}")
        sys.exit(1)

    # --view
    if args.view:
        conv = next((c for c in conversations if c["id"] == args.view or c["id"].startswith(args.view)), None)
        if not conv:
            print(f"{C.RED}Conversation not found: {args.view}{C.RESET}")
            sys.exit(1)
        print_conversation_detail(conv)
        print_conversation_messages(Path(conv["file_path"]))
        return

    # --resume
    if args.resume:
        conv = next((c for c in conversations if c["id"] == args.resume or c["id"].startswith(args.resume)), None)
        if not conv:
            print(f"{C.RED}Conversation not found: {args.resume}{C.RESET}")
            sys.exit(1)
        print_conversation_detail(conv)
        print(f"  {C.PURPLE}Launching:{C.RESET} claude --resume {conv['id']}\n")
        os.execvp("claude", ["claude", "--resume", conv["id"]])
        return

    # Apply filters
    if args.search:
        q = args.search.lower()
        conversations = [
            c for c in conversations
            if q in (c.get("title") or "").lower()
            or q in (c.get("project_path") or "").lower()
            or any(q in m.lower() for m in c.get("models", []))
            or q in c.get("id", "").lower()
        ]

    if args.project:
        q = args.project.lower()
        conversations = [
            c for c in conversations
            if q in (c.get("project_path") or "").lower()
            or q in (c.get("project") or "").lower()
        ]

    if not conversations:
        print(f"{C.YELLOW}No conversations match the filters.{C.RESET}")
        sys.exit(0)

    # --list (non-interactive)
    if args.list:
        conversations = conversations[:args.limit]
        print_conversation_list(conversations, page=0, page_size=len(conversations))
        print(f"  {C.DIM}Resume any conversation:{C.RESET} claude --resume <session-id>")
        if _update_available:
            print(f"  {C.LAVENDER}✦ Update available! Run: {C.GOLD}pip install --upgrade claude-conversation-viewer{C.RESET}")
        print()
        return

    # Interactive mode (default)
    interactive_mode(conversations)


if __name__ == "__main__":
    main()
