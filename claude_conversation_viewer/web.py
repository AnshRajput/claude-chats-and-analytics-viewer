#!/usr/bin/env python3
"""
Claude Code Conversation Viewer
================================
A single-file, zero-dependency GUI for browsing your Claude Code conversation history.

Usage:
    python3 claude_conversation_viewer.py [--port PORT] [--no-open]

Requirements: Python 3.7+ (no pip install needed)
Works on: macOS, Windows, Linux
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse, parse_qs

try:
    from claude_conversation_viewer.update_checker import check_for_update_sync
except ImportError:
    check_for_update_sync = None

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


def decode_project_slug(slug: str) -> str:
    """Best-effort decode of project slug to path. The slug replaces / with -."""
    # The slug is the cwd with / replaced by -
    # We can't perfectly reverse it, but we use cwd from conversations when available
    return slug

# ---------------------------------------------------------------------------
# JSONL parser
# ---------------------------------------------------------------------------

def parse_conversation_metadata(filepath: Path) -> dict | None:
    """Fast scan: read only what we need for the list view."""
    session_id = filepath.stem
    project_slug = filepath.parent.name
    title = None
    first_timestamp = None
    last_timestamp = None
    models = set()
    total_input_tokens = 0
    total_output_tokens = 0
    total_cache_creation = 0
    total_cache_read = 0
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
                        pass  # meta/command messages
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
                    total_cache_creation += usage.get("cache_creation_input_tokens", 0)
                    total_cache_read += usage.get("cache_read_input_tokens", 0)

    except (OSError, PermissionError):
        return None

    if user_msg_count == 0 and assistant_msg_count == 0:
        return None

    return {
        "id": session_id,
        "project": project_slug,
        "project_path": cwd or decode_project_slug(project_slug),
        "title": title or "(no title)",
        "first_timestamp": first_timestamp,
        "last_timestamp": last_timestamp,
        "models": sorted(models),
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_cache_creation": total_cache_creation,
        "total_cache_read": total_cache_read,
        "user_messages": user_msg_count,
        "assistant_messages": assistant_msg_count,
        "total_messages": user_msg_count + assistant_msg_count,
        "cwd": cwd,
        "version": version,
        "file_path": str(filepath),
    }


def parse_full_conversation(filepath: Path) -> list[dict]:
    """Parse full conversation for the viewer."""
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
                msg_type = obj.get("type")

                if role not in ("user", "assistant"):
                    continue

                # Skip meta/command messages for cleaner view
                if role == "user" and obj.get("isMeta"):
                    continue

                content = msg.get("content", "")

                # Skip command messages
                if isinstance(content, str) and content.strip().startswith("<command-name>"):
                    continue
                if isinstance(content, str) and content.strip().startswith("<local-command"):
                    continue

                entry = {
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

                # Process content
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
                                    "id": block.get("id", ""),
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
                                    "tool_use_id": block.get("tool_use_id", ""),
                                    "content": str(result_content)[:5000],
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


def export_as_markdown(filepath: Path, metadata: dict) -> str:
    """Export a conversation as a markdown file."""
    messages = parse_full_conversation(filepath)
    lines = []
    lines.append(f"# {metadata.get('title', 'Conversation')}")
    lines.append("")
    lines.append(f"**Session ID:** {metadata['id']}")
    lines.append(f"**Project:** {metadata['project_path']}")
    lines.append(f"**Date:** {metadata.get('first_timestamp', 'Unknown')}")
    if metadata.get("models"):
        lines.append(f"**Model(s):** {', '.join(metadata['models'])}")
    lines.append(f"**Messages:** {metadata['total_messages']}")
    lines.append("")
    lines.append("---")
    lines.append("")

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
                lines.append(f"<details><summary>Tool: {block['name']}</summary>")
                lines.append("")
                lines.append("```json")
                lines.append(json.dumps(block.get("input", {}), indent=2)[:3000])
                lines.append("```")
                lines.append("</details>")
            elif btype == "tool_result":
                lines.append(f"<details><summary>Tool Result</summary>")
                lines.append("")
                lines.append("```")
                lines.append(str(block.get("content", ""))[:3000])
                lines.append("```")
                lines.append("</details>")
            lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Data store (built on startup)
# ---------------------------------------------------------------------------

class ConversationStore:
    def __init__(self):
        self.conversations: list[dict] = []
        self.by_id: dict[str, dict] = {}
        self.projects: list[str] = []
        self._file_map: dict[str, Path] = {}

    def load(self):
        projects_dir = get_projects_dir()
        if not projects_dir.exists():
            print(f"[WARN] Claude projects directory not found: {projects_dir}")
            return

        print(f"[INFO] Scanning {projects_dir} ...")
        count = 0
        for project_dir in sorted(projects_dir.iterdir()):
            if not project_dir.is_dir():
                continue
            if project_dir.name.startswith("."):
                continue
            for jsonl_file in sorted(project_dir.glob("*.jsonl")):
                meta = parse_conversation_metadata(jsonl_file)
                if meta:
                    self.conversations.append(meta)
                    self.by_id[meta["id"]] = meta
                    self._file_map[meta["id"]] = jsonl_file
                    count += 1

        # Sort newest first
        self.conversations.sort(
            key=lambda c: c.get("last_timestamp") or "", reverse=True
        )
        self.projects = sorted(set(c["project"] for c in self.conversations))
        print(f"[INFO] Loaded {count} conversations from {len(self.projects)} projects")

    def get_stats(self) -> dict:
        total_input = sum(c["total_input_tokens"] for c in self.conversations)
        total_output = sum(c["total_output_tokens"] for c in self.conversations)
        total_cache_create = sum(c["total_cache_creation"] for c in self.conversations)
        total_cache_read = sum(c["total_cache_read"] for c in self.conversations)
        total_messages = sum(c["total_messages"] for c in self.conversations)

        model_counts: dict[str, int] = {}
        for c in self.conversations:
            for m in c["models"]:
                model_counts[m] = model_counts.get(m, 0) + 1

        project_counts: dict[str, int] = {}
        for c in self.conversations:
            p = c["project_path"]
            project_counts[p] = project_counts.get(p, 0) + 1

        return {
            "total_conversations": len(self.conversations),
            "total_projects": len(self.projects),
            "total_messages": total_messages,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_cache_creation_tokens": total_cache_create,
            "total_cache_read_tokens": total_cache_read,
            "total_tokens": total_input + total_output + total_cache_create + total_cache_read,
            "model_usage": dict(sorted(model_counts.items(), key=lambda x: -x[1])),
            "project_counts": dict(sorted(project_counts.items(), key=lambda x: -x[1])),
        }


STORE = ConversationStore()

# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress default logging

    def _json_response(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html_response(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _download_response(self, content, filename, content_type="text/markdown"):
        body = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/":
            self._html_response(HTML_PAGE)

        elif path == "/api/conversations":
            self._json_response({
                "conversations": STORE.conversations,
                "projects": STORE.projects,
            })

        elif path.startswith("/api/conversation/"):
            conv_id = path.split("/")[-1]
            if conv_id not in STORE.by_id:
                self._json_response({"error": "Not found"}, 404)
                return
            filepath = STORE._file_map[conv_id]
            messages = parse_full_conversation(filepath)
            self._json_response({
                "metadata": STORE.by_id[conv_id],
                "messages": messages,
            })

        elif path.startswith("/api/export/"):
            conv_id = path.split("/")[-1]
            fmt = params.get("format", ["md"])[0]
            if conv_id not in STORE.by_id:
                self._json_response({"error": "Not found"}, 404)
                return
            filepath = STORE._file_map[conv_id]
            meta = STORE.by_id[conv_id]
            if fmt == "json":
                messages = parse_full_conversation(filepath)
                content = json.dumps({"metadata": meta, "messages": messages}, indent=2)
                self._download_response(content, f"{conv_id}.json", "application/json")
            else:
                md = export_as_markdown(filepath, meta)
                self._download_response(md, f"{conv_id}.md")

        elif path == "/api/stats":
            self._json_response(STORE.get_stats())

        elif path == "/api/update-check":
            if check_for_update_sync is not None:
                self._json_response(check_for_update_sync())
            else:
                self._json_response({"update_available": False})

        else:
            self._json_response({"error": "Not found"}, 404)

# ---------------------------------------------------------------------------
# Embedded Frontend
# ---------------------------------------------------------------------------

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Claude Code Conversations</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.1/marked.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg: #0d1117;
  --bg-secondary: #161b22;
  --bg-tertiary: #1c2128;
  --border: #30363d;
  --text: #e6edf3;
  --text-muted: #8b949e;
  --accent: #c084fc;
  --accent-dim: rgba(192, 132, 252, 0.15);
  --user-bg: #1a1f2e;
  --assistant-bg: #161b22;
  --success: #3fb950;
  --warning: #d29922;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
  background: var(--bg);
  color: var(--text);
  height: 100vh;
  overflow: hidden;
}

.app {
  display: flex;
  height: 100vh;
}

/* ---- Sidebar ---- */
.sidebar {
  width: 360px;
  min-width: 360px;
  background: var(--bg-secondary);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  height: 100vh;
}

.sidebar-header {
  padding: 16px;
  border-bottom: 1px solid var(--border);
}

.sidebar-header h1 {
  font-size: 16px;
  font-weight: 600;
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.sidebar-header h1 .logo {
  color: var(--accent);
}

.tabs {
  display: flex;
  gap: 4px;
  margin-bottom: 12px;
}

.tab-btn {
  flex: 1;
  padding: 6px 12px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text-muted);
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  transition: all 0.15s;
}

.tab-btn.active {
  background: var(--accent-dim);
  color: var(--accent);
  border-color: var(--accent);
}

.tab-btn:hover:not(.active) {
  background: var(--bg-tertiary);
  color: var(--text);
}

.search-box {
  width: 100%;
  padding: 8px 12px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text);
  font-size: 13px;
  outline: none;
}

.search-box:focus {
  border-color: var(--accent);
}

.search-box::placeholder { color: var(--text-muted); }

.filter-row {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}

.filter-select {
  min-width: 0;
  padding: 6px 8px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text);
  font-size: 12px;
  outline: none;
}

#projectFilter { flex: 1.2; }
#sortSelect { flex: 0.8; }

.conv-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.conv-item {
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  border: 1px solid transparent;
  margin-bottom: 4px;
  transition: all 0.15s;
}

.conv-item:hover {
  background: var(--bg-tertiary);
}

.conv-item.active {
  background: var(--accent-dim);
  border-color: var(--accent);
}

.conv-title {
  font-size: 13px;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 4px;
}

.conv-meta {
  display: flex;
  gap: 8px;
  font-size: 11px;
  color: var(--text-muted);
  flex-wrap: wrap;
}

.conv-meta span {
  display: flex;
  align-items: center;
  gap: 3px;
}

.badge {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 10px;
  font-size: 10px;
  font-weight: 500;
  background: var(--bg-tertiary);
  border: 1px solid var(--border);
}

.conv-id {
  font-size: 10px;
  font-family: monospace;
  color: var(--text-muted);
  opacity: 0.7;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 3px;
}

.conv-project {
  font-size: 11px;
  color: var(--accent);
  opacity: 0.8;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 2px;
}

/* ---- Main panel ---- */
.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.main-header {
  padding: 0;
  border-bottom: 1px solid var(--border);
  background: var(--bg-secondary);
}

.main-header-top {
  display: flex;
  align-items: center;
  padding: 10px 20px;
  min-height: 48px;
}

.main-header-title {
  font-size: 14px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
}

.main-header-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.main-header-session {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 20px 8px;
  border-top: 1px solid var(--border);
  background: var(--bg-tertiary);
  font-size: 12px;
  flex-wrap: wrap;
}

.session-label {
  color: var(--text-muted);
  font-size: 11px;
  flex-shrink: 0;
}

.session-id {
  font-family: monospace;
  font-size: 12px;
  color: var(--cyan, #79c0ff);
  background: var(--bg);
  padding: 2px 8px;
  border-radius: 4px;
  border: 1px solid var(--border);
  user-select: all;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

.btn-sm {
  padding: 3px 10px;
  font-size: 11px;
  flex-shrink: 0;
}

.btn {
  padding: 6px 14px;
  border: 1px solid var(--border);
  background: var(--bg-tertiary);
  color: var(--text);
  border-radius: 6px;
  cursor: pointer;
  font-size: 12px;
  transition: all 0.15s;
}

.btn:hover {
  background: var(--accent-dim);
  border-color: var(--accent);
  color: var(--accent);
}

.messages-container {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--text-muted);
  font-size: 14px;
  gap: 8px;
}

.empty-state .big { font-size: 40px; opacity: 0.3; }

/* ---- Messages ---- */
.message {
  margin-bottom: 20px;
  max-width: 900px;
}

.message.user {
  background: var(--user-bg);
  border: 1px solid rgba(99, 102, 241, 0.2);
  border-radius: 12px;
  padding: 14px 18px;
}

.message.assistant {
  background: var(--assistant-bg);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 14px 18px;
}

.message-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
  font-size: 12px;
}

.message-role {
  font-weight: 600;
  text-transform: uppercase;
  font-size: 11px;
  letter-spacing: 0.5px;
}

.message.user .message-role { color: #818cf8; }
.message.assistant .message-role { color: var(--accent); }

.message-time {
  color: var(--text-muted);
  font-size: 11px;
}

.message-body {
  font-size: 14px;
  line-height: 1.6;
}

.message-body p { margin-bottom: 8px; }
.message-body p:last-child { margin-bottom: 0; }

.message-body pre {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 12px;
  overflow-x: auto;
  margin: 8px 0;
  font-size: 13px;
}

.message-body code {
  background: var(--bg);
  padding: 2px 5px;
  border-radius: 3px;
  font-size: 13px;
}

.message-body pre code {
  background: none;
  padding: 0;
}

.message-body ul, .message-body ol {
  margin: 8px 0;
  padding-left: 24px;
}

.message-body li { margin-bottom: 4px; }

.message-body blockquote {
  border-left: 3px solid var(--accent);
  padding-left: 12px;
  color: var(--text-muted);
  margin: 8px 0;
}

.message-body h1, .message-body h2, .message-body h3, .message-body h4 {
  margin: 12px 0 6px 0;
}

.message-body table {
  border-collapse: collapse;
  margin: 8px 0;
  width: 100%;
}

.message-body th, .message-body td {
  border: 1px solid var(--border);
  padding: 6px 10px;
  font-size: 13px;
}

.message-body th {
  background: var(--bg-tertiary);
}

/* Tool blocks */
.tool-block {
  margin: 8px 0;
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow: hidden;
}

.tool-header {
  padding: 8px 12px;
  background: var(--bg-tertiary);
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  font-weight: 500;
  user-select: none;
}

.tool-header:hover { background: var(--bg); }

.tool-header .arrow {
  transition: transform 0.15s;
  font-size: 10px;
  color: var(--text-muted);
}

.tool-header .arrow.open { transform: rotate(90deg); }

.tool-name {
  color: var(--accent);
  font-family: monospace;
}

.tool-body {
  padding: 10px 12px;
  border-top: 1px solid var(--border);
  font-size: 12px;
  max-height: 400px;
  overflow: auto;
}

.tool-body pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 12px;
}

.tool-result-block {
  margin: 8px 0;
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow: hidden;
}

.tool-result-header {
  padding: 6px 12px;
  background: rgba(63, 185, 80, 0.1);
  border-bottom: 1px solid var(--border);
  font-size: 11px;
  color: var(--success);
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 8px;
  user-select: none;
}

.tool-result-body {
  padding: 8px 12px;
  font-size: 12px;
  max-height: 300px;
  overflow: auto;
}

.tool-result-body pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 12px;
}

.token-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border);
  border-radius: 10px;
  font-size: 11px;
  color: var(--text-muted);
}

/* ---- Stats panel ---- */
.stats-panel {
  padding: 24px;
  overflow-y: auto;
  height: 100%;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}

.stat-card {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px;
}

.stat-value {
  font-size: 28px;
  font-weight: 700;
  color: var(--accent);
}

.stat-label {
  font-size: 12px;
  color: var(--text-muted);
  margin-top: 4px;
}

.stats-section {
  margin-bottom: 24px;
}

.stats-section h3 {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 12px;
  color: var(--text);
}

.stats-bar-chart {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.bar-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.bar-label {
  min-width: 200px;
  font-size: 12px;
  color: var(--text-muted);
  text-align: right;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.bar-track {
  flex: 1;
  height: 24px;
  background: var(--bg);
  border-radius: 4px;
  overflow: hidden;
}

.bar-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--accent), #a855f7);
  border-radius: 4px;
  display: flex;
  align-items: center;
  padding-left: 8px;
  font-size: 11px;
  font-weight: 500;
  min-width: fit-content;
}

/* ---- Loading ---- */
.loading {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px;
  color: var(--text-muted);
}

.spinner {
  width: 20px;
  height: 20px;
  border: 2px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
  margin-right: 10px;
}

@keyframes spin { to { transform: rotate(360deg); } }

/* ---- Scrollbar ---- */
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: var(--text-muted); }

/* ---- Back button (mobile) ---- */
.back-btn {
  display: none;
  padding: 6px 10px;
  border: 1px solid var(--border);
  background: var(--bg-tertiary);
  color: var(--text);
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  margin-right: 10px;
  flex-shrink: 0;
  transition: all 0.15s;
}

.back-btn:hover {
  background: var(--accent-dim);
  border-color: var(--accent);
  color: var(--accent);
}

/* ---- Responsive: tablets ---- */
@media (max-width: 1024px) {
  .sidebar { width: 300px; min-width: 300px; }
  .message { max-width: 100%; }
  .stats-grid { grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); }
  .bar-label { min-width: 140px; }
}

/* ---- Responsive: mobile ---- */
@media (max-width: 768px) {
  .sidebar { width: 100%; min-width: 100%; }
  .main { display: none; }
  .app.conv-open .sidebar { display: none; }
  .app.conv-open .main { display: flex; }
  .back-btn { display: block; }

  .main-header-top {
    padding: 8px 12px;
  }

  .main-header-title {
    font-size: 13px;
  }

  .main-header-session {
    padding: 6px 12px;
    gap: 6px;
  }

  .session-id {
    font-size: 11px;
    max-width: 200px;
  }

  .main-header-actions { gap: 4px; }
  .btn { padding: 5px 10px; font-size: 11px; }

  .messages-container { padding: 12px; }
  .message { padding: 10px 14px; }
  .message-body { font-size: 13px; }
  .message-header { flex-wrap: wrap; gap: 4px; }

  .stats-panel { padding: 16px; }
  .stats-grid { grid-template-columns: repeat(2, 1fr); gap: 10px; }
  .stat-card { padding: 12px; }
  .stat-value { font-size: 22px; }
  .bar-label { min-width: 100px; font-size: 11px; }
  .bar-fill { font-size: 10px; }

  .sidebar-header h1 { font-size: 14px; }
  .filter-row { flex-direction: column; gap: 6px; }
  #projectFilter, #sortSelect { flex: unset; width: 100%; }
}

/* ---- Responsive: very small ---- */
@media (max-width: 480px) {
  .sidebar-header { padding: 12px; }
  .conv-list { padding: 4px; }
  .conv-item { padding: 8px 10px; }
  .stats-grid { grid-template-columns: 1fr 1fr; gap: 8px; }
  .stat-value { font-size: 18px; }
  .bar-row { flex-direction: column; align-items: stretch; gap: 2px; }
  .bar-label { min-width: unset; text-align: left; font-size: 11px; }
}

/* ---- Update banner ---- */
.update-banner {
  background: var(--accent-dim);
  border-bottom: 1px solid var(--accent);
  color: var(--text);
  padding: 8px 16px;
  font-size: 13px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  z-index: 100;
}
.update-banner code {
  background: var(--bg-tertiary);
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 12px;
  color: var(--accent);
}
.update-banner .dismiss-btn {
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 16px;
  padding: 0 4px;
  line-height: 1;
}
.update-banner .dismiss-btn:hover {
  color: var(--text);
}
</style>
</head>
<body>
<div class="update-banner" id="updateBanner" style="display:none">
  <span>&#10022; Update available! Run <code>pip install --upgrade claude-conversation-viewer</code> to update.</span>
  <button class="dismiss-btn" onclick="dismissUpdate()" title="Dismiss">&times;</button>
</div>
<div class="app" id="app">
  <!-- Sidebar -->
  <div class="sidebar">
    <div class="sidebar-header">
      <h1><span class="logo">&#9670;</span> Claude Code Conversations</h1>
      <div class="tabs">
        <button class="tab-btn active" data-tab="conversations" onclick="switchTab('conversations')">Conversations</button>
        <button class="tab-btn" data-tab="stats" onclick="switchTab('stats')">Stats</button>
      </div>
      <input type="text" class="search-box" id="searchBox" placeholder="Search conversations..." oninput="filterConversations()">
      <div class="filter-row">
        <select class="filter-select" id="projectFilter" onchange="filterConversations()">
          <option value="">All projects</option>
        </select>
        <select class="filter-select" id="sortSelect" onchange="filterConversations()">
          <option value="newest">Newest first</option>
          <option value="oldest">Oldest first</option>
          <option value="most-messages">Most messages</option>
          <option value="most-tokens">Most tokens</option>
        </select>
      </div>
    </div>
    <div class="conv-list" id="convList">
      <div class="loading"><div class="spinner"></div> Loading conversations...</div>
    </div>
  </div>

  <!-- Main -->
  <div class="main" id="mainPanel">
    <div class="main-header" id="mainHeader">
      <div class="main-header-top">
        <button class="back-btn" onclick="goBack()">&larr; Back</button>
        <span class="main-header-title" id="mainTitle">Select a conversation</span>
        <div class="main-header-actions" id="mainActions" style="display:none">
          <button class="btn" onclick="exportConversation('md')">Export .md</button>
          <button class="btn" onclick="exportConversation('json')">Export .json</button>
        </div>
      </div>
      <div class="main-header-session" id="sessionBar" style="display:none">
        <span class="session-label">Session ID:</span>
        <code class="session-id" id="sessionIdText"></code>
        <button class="btn btn-sm" onclick="copyResume()" id="copyBtn">Copy resume command</button>
      </div>
    </div>
    <div class="messages-container" id="messagesContainer">
      <div class="empty-state">
        <div class="big">&#9670;</div>
        <div>Select a conversation from the sidebar</div>
      </div>
    </div>
    <!-- Stats (hidden by default) -->
    <div class="stats-panel" id="statsPanel" style="display:none">
      <div class="loading"><div class="spinner"></div> Loading stats...</div>
    </div>
  </div>
</div>

<script>
// ---- State ----
let allConversations = [];
let allProjects = [];
let currentConvId = null;
let currentTab = 'conversations';

// ---- Init ----
document.addEventListener('DOMContentLoaded', init);

async function init() {
  const res = await fetch('/api/conversations');
  const data = await res.json();
  allConversations = data.conversations;
  allProjects = data.projects;

  // Build project display name map from conversation cwd values
  const projectDisplayNames = {};
  allConversations.forEach(c => {
    if (!projectDisplayNames[c.project]) {
      projectDisplayNames[c.project] = c.project_path || decodeProject(c.project);
    }
  });

  // Populate project filter
  const sel = document.getElementById('projectFilter');
  allProjects.forEach(p => {
    const opt = document.createElement('option');
    opt.value = p;
    opt.textContent = shortenPath(projectDisplayNames[p]) || p;
    sel.appendChild(opt);
  });

  renderConversationList(allConversations);

  // Check for updates
  if (!sessionStorage.getItem('update-dismissed')) {
    checkForUpdate();
  }
}

// ---- Update Check ----
async function checkForUpdate() {
  try {
    const res = await fetch('/api/update-check');
    const data = await res.json();
    if (data.update_available) {
      document.getElementById('updateBanner').style.display = 'flex';
    }
  } catch (e) {
    // silently ignore
  }
}

function dismissUpdate() {
  document.getElementById('updateBanner').style.display = 'none';
  sessionStorage.setItem('update-dismissed', '1');
}

function decodeProject(slug) {
  return slug.replace(/-/g, '/');
}

function shortenPath(fullPath) {
  if (!fullPath) return '';
  // Show just the last 2 meaningful segments: e.g. "engineering/myproject" or "~/myproject"
  const parts = fullPath.replace(/\\/g, '/').split('/').filter(Boolean);
  const home = parts.indexOf('Users') >= 0 ? parts.slice(parts.indexOf('Users') + 2) : parts;
  if (home.length === 0) return '~';
  if (home.length <= 2) return '~/' + home.join('/');
  return home.slice(-2).join('/');
}

function formatDate(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  const now = new Date();
  const diffMs = now - d;
  const diffDays = Math.floor(diffMs / 86400000);
  if (diffDays === 0) return 'Today ' + d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
  if (diffDays === 1) return 'Yesterday';
  if (diffDays < 7) return d.toLocaleDateString([], {weekday: 'short'});
  return d.toLocaleDateString([], {month: 'short', day: 'numeric', year: 'numeric'});
}

function formatTokens(n) {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
  return n.toString();
}

// ---- Tabs ----
function switchTab(tab) {
  currentTab = tab;
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));

  const mainHeader = document.getElementById('mainHeader');
  const msgContainer = document.getElementById('messagesContainer');
  const statsPanel = document.getElementById('statsPanel');

  if (tab === 'conversations') {
    mainHeader.style.display = 'block';
    msgContainer.style.display = 'block';
    statsPanel.style.display = 'none';
    // Hide session bar if no conversation is selected
    if (!currentConvId) {
      document.getElementById('sessionBar').style.display = 'none';
    }
  } else {
    mainHeader.style.display = 'none';
    msgContainer.style.display = 'none';
    statsPanel.style.display = 'block';
    loadStats();
  }
}

// ---- Filter & Search ----
function filterConversations() {
  const query = document.getElementById('searchBox').value.toLowerCase();
  const project = document.getElementById('projectFilter').value;
  const sort = document.getElementById('sortSelect').value;

  let filtered = allConversations;

  if (project) {
    filtered = filtered.filter(c => c.project === project);
  }

  if (query) {
    filtered = filtered.filter(c =>
      (c.title || '').toLowerCase().includes(query) ||
      (c.project_path || '').toLowerCase().includes(query) ||
      (c.models || []).some(m => m.toLowerCase().includes(query))
    );
  }

  // Sort
  if (sort === 'newest') {
    filtered.sort((a, b) => (b.last_timestamp || '').localeCompare(a.last_timestamp || ''));
  } else if (sort === 'oldest') {
    filtered.sort((a, b) => (a.first_timestamp || '').localeCompare(b.first_timestamp || ''));
  } else if (sort === 'most-messages') {
    filtered.sort((a, b) => b.total_messages - a.total_messages);
  } else if (sort === 'most-tokens') {
    filtered.sort((a, b) => (b.total_input_tokens + b.total_output_tokens) - (a.total_input_tokens + a.total_output_tokens));
  }

  renderConversationList(filtered);
}

// ---- Render conversation list ----
function renderConversationList(convs) {
  const container = document.getElementById('convList');
  if (convs.length === 0) {
    container.innerHTML = '<div class="empty-state"><div>No conversations found</div></div>';
    return;
  }

  container.innerHTML = convs.map(c => `
    <div class="conv-item ${c.id === currentConvId ? 'active' : ''}" onclick="loadConversation('${c.id}')">
      <div class="conv-project" title="${escapeHtml(c.project_path || '')}">${escapeHtml(shortenPath(c.project_path) || c.project)}</div>
      <div class="conv-title">${escapeHtml(c.title)}</div>
      <div class="conv-id">${c.id}</div>
      <div class="conv-meta">
        <span>${formatDate(c.first_timestamp)}</span>
        <span>${c.total_messages} msgs</span>
        ${c.models.length ? `<span class="badge">${escapeHtml(c.models[0])}</span>` : ''}
        <span>${formatTokens(c.total_input_tokens + c.total_output_tokens)} tok</span>
      </div>
    </div>
  `).join('');
}

// ---- Load conversation ----
async function loadConversation(id) {
  currentConvId = id;
  document.getElementById('app').classList.add('conv-open');

  // Update active state
  document.querySelectorAll('.conv-item').forEach(el => {
    el.classList.toggle('active', el.onclick.toString().includes(id));
  });

  const container = document.getElementById('messagesContainer');
  container.innerHTML = '<div class="loading"><div class="spinner"></div> Loading conversation...</div>';
  document.getElementById('mainActions').style.display = 'flex';

  const res = await fetch(`/api/conversation/${id}`);
  const data = await res.json();
  const meta = data.metadata;
  const msgs = data.messages;

  document.getElementById('mainTitle').textContent = meta.title;
  document.getElementById('sessionBar').style.display = 'flex';
  document.getElementById('sessionIdText').textContent = meta.id;

  if (msgs.length === 0) {
    container.innerHTML = '<div class="empty-state"><div>No messages in this conversation</div></div>';
    return;
  }

  container.innerHTML = msgs.map(renderMessage).join('');

  // Highlight code blocks
  if (typeof hljs !== 'undefined') {
    container.querySelectorAll('pre code').forEach(el => {
      try { hljs.highlightElement(el); } catch(e) {}
    });
  }

  container.scrollTop = 0;
}

function renderMessage(msg) {
  const role = msg.role;
  const time = msg.timestamp ? new Date(msg.timestamp).toLocaleString() : '';
  const content = msg.content || [];

  let usageBadge = '';
  if (role === 'assistant' && msg.usage) {
    const u = msg.usage;
    const total = u.input_tokens + u.output_tokens;
    if (total > 0) {
      usageBadge = `<span class="token-badge">in: ${formatTokens(u.input_tokens)} / out: ${formatTokens(u.output_tokens)}</span>`;
    }
  }

  const modelBadge = msg.model ? `<span class="badge">${escapeHtml(msg.model)}</span>` : '';

  let bodyHtml = content.map(block => {
    if (block.type === 'text') {
      return renderMarkdown(block.text);
    } else if (block.type === 'tool_use') {
      return renderToolUse(block);
    } else if (block.type === 'tool_result') {
      return renderToolResult(block);
    }
    return '';
  }).join('');

  return `
    <div class="message ${role}">
      <div class="message-header">
        <span>
          <span class="message-role">${role}</span>
          ${modelBadge}
        </span>
        <span>
          ${usageBadge}
          <span class="message-time">${escapeHtml(time)}</span>
        </span>
      </div>
      <div class="message-body">${bodyHtml}</div>
    </div>
  `;
}

function renderToolUse(block) {
  const inputStr = JSON.stringify(block.input, null, 2);
  const toolId = 'tool-' + Math.random().toString(36).substr(2, 9);
  // Show a summary of the input
  let summary = '';
  if (block.name === 'Bash' && block.input.command) {
    summary = ': ' + escapeHtml(block.input.command.substring(0, 80));
  } else if (block.name === 'Read' && block.input.file_path) {
    summary = ': ' + escapeHtml(block.input.file_path);
  } else if (block.name === 'Write' && block.input.file_path) {
    summary = ': ' + escapeHtml(block.input.file_path);
  } else if (block.name === 'Edit' && block.input.file_path) {
    summary = ': ' + escapeHtml(block.input.file_path);
  } else if (block.name === 'Grep' && block.input.pattern) {
    summary = ': ' + escapeHtml(block.input.pattern);
  } else if (block.name === 'Glob' && block.input.pattern) {
    summary = ': ' + escapeHtml(block.input.pattern);
  }

  return `
    <div class="tool-block">
      <div class="tool-header" onclick="toggleTool('${toolId}')">
        <span class="arrow" id="${toolId}-arrow">&#9654;</span>
        <span class="tool-name">${escapeHtml(block.name)}</span>${summary}
      </div>
      <div class="tool-body" id="${toolId}" style="display:none">
        <pre>${escapeHtml(inputStr.substring(0, 5000))}</pre>
      </div>
    </div>
  `;
}

function renderToolResult(block) {
  const toolId = 'result-' + Math.random().toString(36).substr(2, 9);
  const content = block.content || '(empty)';
  return `
    <div class="tool-result-block">
      <div class="tool-result-header" onclick="toggleTool('${toolId}')">
        <span class="arrow" id="${toolId}-arrow">&#9654;</span>
        Tool Result
      </div>
      <div class="tool-result-body" id="${toolId}" style="display:none">
        <pre>${escapeHtml(content)}</pre>
      </div>
    </div>
  `;
}

function toggleTool(id) {
  const el = document.getElementById(id);
  const arrow = document.getElementById(id + '-arrow');
  if (el.style.display === 'none') {
    el.style.display = 'block';
    arrow.classList.add('open');
  } else {
    el.style.display = 'none';
    arrow.classList.remove('open');
  }
}

function renderMarkdown(text) {
  if (typeof marked !== 'undefined') {
    try {
      return marked.parse(text);
    } catch(e) {}
  }
  // Fallback: basic escaping with line breaks
  return '<p>' + escapeHtml(text).replace(/\n/g, '<br>') + '</p>';
}

function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ---- Back (mobile) ----
function goBack() {
  document.getElementById('app').classList.remove('conv-open');
  currentConvId = null;
}

// ---- Copy resume command ----
function copyResume() {
  if (!currentConvId) return;
  const cmd = `claude --resume ${currentConvId}`;
  navigator.clipboard.writeText(cmd).then(() => {
    const btn = document.getElementById('copyBtn');
    btn.textContent = 'Copied!';
    setTimeout(() => { btn.textContent = 'Copy resume command'; }, 2000);
  });
}

// ---- Export ----
function exportConversation(format) {
  if (!currentConvId) return;
  window.open(`/api/export/${currentConvId}?format=${format}`, '_blank');
}

// ---- Stats ----
async function loadStats() {
  const panel = document.getElementById('statsPanel');
  panel.innerHTML = '<div class="loading"><div class="spinner"></div> Loading stats...</div>';

  const res = await fetch('/api/stats');
  const stats = await res.json();

  const modelBars = Object.entries(stats.model_usage).map(([model, count]) => {
    const maxCount = Math.max(...Object.values(stats.model_usage));
    const pct = (count / maxCount * 100).toFixed(0);
    return `
      <div class="bar-row">
        <div class="bar-label">${escapeHtml(model)}</div>
        <div class="bar-track"><div class="bar-fill" style="width: ${pct}%">${count}</div></div>
      </div>
    `;
  }).join('');

  const projectBars = Object.entries(stats.project_counts).slice(0, 10).map(([proj, count]) => {
    const maxCount = Math.max(...Object.values(stats.project_counts));
    const pct = (count / maxCount * 100).toFixed(0);
    return `
      <div class="bar-row">
        <div class="bar-label" title="${escapeHtml(proj)}">${escapeHtml(shortenPath(proj))}</div>
        <div class="bar-track"><div class="bar-fill" style="width: ${pct}%">${count}</div></div>
      </div>
    `;
  }).join('');

  panel.innerHTML = `
    <h2 style="margin-bottom: 20px; font-size: 20px;">Usage Statistics</h2>
    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-value">${stats.total_conversations}</div>
        <div class="stat-label">Total Conversations</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">${stats.total_projects}</div>
        <div class="stat-label">Projects</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">${formatTokens(stats.total_messages)}</div>
        <div class="stat-label">Total Messages</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">${formatTokens(stats.total_tokens)}</div>
        <div class="stat-label">Total Tokens</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">${formatTokens(stats.total_input_tokens)}</div>
        <div class="stat-label">Input Tokens</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">${formatTokens(stats.total_output_tokens)}</div>
        <div class="stat-label">Output Tokens</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">${formatTokens(stats.total_cache_creation_tokens)}</div>
        <div class="stat-label">Cache Creation Tokens</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">${formatTokens(stats.total_cache_read_tokens)}</div>
        <div class="stat-label">Cache Read Tokens</div>
      </div>
    </div>
    <div class="stats-section">
      <h3>Model Usage (conversations per model)</h3>
      <div class="stats-bar-chart">${modelBars || '<div style="color:var(--text-muted)">No data</div>'}</div>
    </div>
    <div class="stats-section">
      <h3>Top Projects (conversations per project)</h3>
      <div class="stats-bar-chart">${projectBars || '<div style="color:var(--text-muted)">No data</div>'}</div>
    </div>
  `;
}
</script>
</body>
</html>"""

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _get_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "com.claude-conversation-viewer.plist"


def _get_script_path() -> str:
    return os.path.abspath(__file__)


def install_service(port: int):
    """Install a macOS LaunchAgent to auto-start on login."""
    if platform.system() != "Darwin":
        print("[ERROR] Auto-start is currently supported on macOS only.")
        print("        On Linux, add this to your crontab:")
        print(f"        @reboot python3 {_get_script_path()} --port {port} --no-open &")
        return

    plist_path = _get_plist_path()
    script = _get_script_path()
    python = sys.executable

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.claude-conversation-viewer</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>{script}</string>
        <string>--port</string>
        <string>{port}</string>
        <string>--no-open</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>/tmp/claude-conversation-viewer.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/claude-conversation-viewer.log</string>
</dict>
</plist>
"""
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(plist_content)

    # Load the service
    os.system(f"launchctl unload {plist_path} 2>/dev/null")
    os.system(f"launchctl load {plist_path}")

    print(f"\n  Service installed and started!")
    print(f"  ================================")
    print(f"  URL:       http://127.0.0.1:{port}")
    print(f"  Plist:     {plist_path}")
    print(f"  Log:       /tmp/claude-conversation-viewer.log")
    print(f"  Auto-starts on login.\n")
    print(f"  To stop:   python3 {script} --uninstall")
    print(f"  To check:  launchctl list | grep claude-conversation\n")


def uninstall_service():
    """Remove the macOS LaunchAgent."""
    if platform.system() != "Darwin":
        print("[INFO] Remove the crontab entry manually: crontab -e")
        return

    plist_path = _get_plist_path()
    if plist_path.exists():
        os.system(f"launchctl unload {plist_path} 2>/dev/null")
        plist_path.unlink()
        print("\n  Service stopped and removed.\n")
    else:
        print("\n  No service installed.\n")


def main():
    parser = argparse.ArgumentParser(description="Claude Code Conversation Viewer")
    parser.add_argument("--port", type=int, default=5005, help="Port to serve on (default: 5005)")
    parser.add_argument("--no-open", action="store_true", help="Don't auto-open browser")
    parser.add_argument("--install", action="store_true", help="Install as background service (auto-start on login)")
    parser.add_argument("--uninstall", action="store_true", help="Remove background service")
    args = parser.parse_args()

    if args.uninstall:
        uninstall_service()
        return

    if args.install:
        install_service(args.port)
        return

    STORE.load()

    server = HTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"

    print(f"\n  Claude Code Conversation Viewer")
    print(f"  ================================")
    print(f"  {len(STORE.conversations)} conversations from {len(STORE.projects)} projects")
    print(f"  Running at: {url}")
    print(f"  Press Ctrl+C to stop")
    print(f"  Tip: run with --install to auto-start on login\n")

    if not args.no_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
