#!/usr/bin/env python3
"""Claude Code Conversation Viewer - Web UI with full feature set."""

from __future__ import annotations

import argparse
import io
import json
import os
import platform
import re
import sys
import tempfile
import threading
import time
import webbrowser
import zipfile
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs

try:
    from claude_conversation_viewer.update_checker import check_for_update_sync
except ImportError:
    check_for_update_sync = None

# ---------------------------------------------------------------------------
# Model pricing table  (input $/1M, output $/1M, cache_write $/1M, cache_read $/1M)
# ---------------------------------------------------------------------------

MODEL_PRICING: Dict[str, Tuple[float, float, float, float]] = {
    "claude-opus-4":     (15.00, 75.00, 18.75, 1.50),
    "claude-sonnet-4":   (3.00,  15.00, 3.75,  0.30),
    "claude-haiku-4":    (0.80,  4.00,  1.00,  0.08),
    "claude-3-7-sonnet": (3.00,  15.00, 3.75,  0.30),
    "claude-3-5-sonnet": (3.00,  15.00, 3.75,  0.30),
    "claude-3-5-haiku":  (0.80,  4.00,  1.00,  0.08),
    "claude-3-opus":     (15.00, 75.00, 18.75, 1.50),
    "claude-3-sonnet":   (3.00,  15.00, 3.75,  0.30),
    "claude-3-haiku":    (0.25,  1.25,  0.30,  0.03),
}

CACHE_VERSION = 2

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def get_claude_dir() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("USERPROFILE", Path.home()))
    else:
        base = Path.home()
    return base / ".claude"


def get_projects_dir() -> Path:
    return get_claude_dir() / "projects"


def get_cache_path() -> Path:
    return Path(tempfile.gettempdir()) / "claude-viewer-cache-v2.json"


def get_bookmarks_path() -> Path:
    return get_claude_dir() / "viewer-bookmarks.json"


def decode_project_slug(slug: str) -> str:
    return slug

# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------

def get_model_pricing(model_name: str) -> Tuple[float, float, float, float]:
    if not model_name:
        return (3.00, 15.00, 3.75, 0.30)
    lower = model_name.lower()
    for pattern, pricing in MODEL_PRICING.items():
        if pattern in lower:
            return pricing
    return (3.00, 15.00, 3.75, 0.30)


def estimate_cost(conv: dict) -> float:
    models = conv.get("models", [])
    pricing = get_model_pricing(models[0] if models else "")
    cost = (
        conv.get("total_input_tokens", 0) / 1_000_000 * pricing[0]
        + conv.get("total_output_tokens", 0) / 1_000_000 * pricing[1]
        + conv.get("total_cache_creation", 0) / 1_000_000 * pricing[2]
        + conv.get("total_cache_read", 0) / 1_000_000 * pricing[3]
    )
    return round(cost, 4)

# ---------------------------------------------------------------------------
# Smart metadata cache
# ---------------------------------------------------------------------------

def load_metadata_cache() -> dict:
    try:
        p = get_cache_path()
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("version") == CACHE_VERSION:
                return data.get("entries", {})
    except Exception:
        pass
    return {}


def save_metadata_cache(entries: dict):
    try:
        get_cache_path().write_text(
            json.dumps({"version": CACHE_VERSION, "entries": entries}),
            encoding="utf-8",
        )
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Bookmarks
# ---------------------------------------------------------------------------

def load_bookmarks() -> dict:
    try:
        p = get_bookmarks_path()
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            return data.get("bookmarks", {})
    except Exception:
        pass
    return {}


def save_bookmarks(bookmarks: dict):
    try:
        p = get_bookmarks_path()
        p.write_text(
            json.dumps({"version": 1, "bookmarks": bookmarks}, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

# ---------------------------------------------------------------------------
# JSONL parser
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
        "project_path": cwd or decode_project_slug(project_slug),
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

# ---------------------------------------------------------------------------
# Data store
# ---------------------------------------------------------------------------

class ConversationStore:
    def __init__(self):
        self.conversations: List[dict] = []
        self.by_id: Dict[str, dict] = {}
        self.projects: List[str] = []
        self._file_map: Dict[str, Path] = {}
        self.bookmarks: Dict[str, bool] = {}
        self.last_scanned: float = 0

    def load(self):
        projects_dir = get_projects_dir()
        if not projects_dir.exists():
            print(f"[WARN] Claude projects directory not found: {projects_dir}")
            self.last_scanned = time.time()
            return

        cache = load_metadata_cache()
        new_cache: dict = {}
        parsed = 0
        cache_hits = 0

        all_files: List[Path] = []
        for project_dir in sorted(projects_dir.iterdir()):
            if not project_dir.is_dir() or project_dir.name.startswith("."):
                continue
            all_files.extend(sorted(project_dir.glob("*.jsonl")))

        conversations: List[dict] = []
        file_map: Dict[str, Path] = {}

        for jsonl_file in all_files:
            file_key = str(jsonl_file)
            try:
                stat = jsonl_file.stat()
                mtime = stat.st_mtime
                size = stat.st_size
            except OSError:
                continue

            cached = cache.get(file_key)
            if cached and cached.get("mtime") == mtime and cached.get("size") == size:
                meta = cached["metadata"]
                cache_hits += 1
            else:
                meta = parse_conversation_metadata(jsonl_file)
                parsed += 1

            if meta:
                new_cache[file_key] = {"mtime": mtime, "size": size, "metadata": meta}
                conversations.append(meta)
                file_map[meta["id"]] = jsonl_file

        conversations.sort(key=lambda c: c.get("last_timestamp") or "", reverse=True)

        self.conversations = conversations
        self.by_id = {c["id"]: c for c in conversations}
        self._file_map = file_map
        self.projects = sorted(set(c["project"] for c in conversations))
        self.bookmarks = load_bookmarks()
        self.last_scanned = time.time()

        save_metadata_cache(new_cache)
        total = len(conversations)
        print(
            f"[INFO] {total} conversations ({cache_hits} cached, {parsed} parsed) "
            f"from {len(self.projects)} projects"
        )

    def get_stats(self) -> dict:
        total_input = sum(c["total_input_tokens"] for c in self.conversations)
        total_output = sum(c["total_output_tokens"] for c in self.conversations)
        total_cache_create = sum(c["total_cache_creation"] for c in self.conversations)
        total_cache_read = sum(c["total_cache_read"] for c in self.conversations)
        total_messages = sum(c["total_messages"] for c in self.conversations)
        total_cost = sum(c.get("estimated_cost_usd", 0) for c in self.conversations)

        model_counts: Dict[str, int] = {}
        for c in self.conversations:
            for m in c["models"]:
                model_counts[m] = model_counts.get(m, 0) + 1

        project_counts: Dict[str, int] = {}
        for c in self.conversations:
            p = c["project_path"]
            project_counts[p] = project_counts.get(p, 0) + 1

        daily_activity: Dict[str, int] = {}
        for c in self.conversations:
            ts = c.get("first_timestamp")
            if ts and len(ts) >= 10:
                day = ts[:10]
                daily_activity[day] = daily_activity.get(day, 0) + 1

        return {
            "total_conversations": len(self.conversations),
            "total_projects": len(self.projects),
            "total_messages": total_messages,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_cache_creation_tokens": total_cache_create,
            "total_cache_read_tokens": total_cache_read,
            "total_tokens": total_input + total_output + total_cache_create + total_cache_read,
            "total_cost_usd": round(total_cost, 4),
            "model_usage": dict(sorted(model_counts.items(), key=lambda x: -x[1])),
            "project_counts": dict(sorted(project_counts.items(), key=lambda x: -x[1])),
            "daily_activity": daily_activity,
        }

    def search_content(self, query: str) -> List[dict]:
        results = []
        query_lower = query.lower()
        for conv in self.conversations:
            filepath = self._file_map.get(conv["id"])
            if not filepath:
                continue
            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        try:
                            obj = json.loads(line.strip())
                            msg = obj.get("message", {})
                            content = msg.get("content", "")
                            if isinstance(content, str):
                                text = content
                            elif isinstance(content, list):
                                text = " ".join(
                                    b.get("text", "") for b in content
                                    if isinstance(b, dict) and b.get("type") == "text"
                                )
                            else:
                                continue

                            if query_lower in text.lower():
                                idx = text.lower().find(query_lower)
                                start = max(0, idx - 60)
                                end = min(len(text), idx + len(query) + 100)
                                prefix = "…" if start > 0 else ""
                                suffix = "…" if end < len(text) else ""
                                snippet = prefix + text[start:end] + suffix
                                results.append({
                                    "id": conv["id"],
                                    "title": conv["title"],
                                    "project_path": conv.get("project_path", ""),
                                    "first_timestamp": conv.get("first_timestamp"),
                                    "snippet": snippet[:300],
                                    "bookmarked": conv["id"] in self.bookmarks,
                                })
                                break
                        except (json.JSONDecodeError, AttributeError):
                            continue
            except (OSError, PermissionError):
                pass
        return results


STORE = ConversationStore()

# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, html: str):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _download(self, content: bytes, filename: str, content_type: str):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/":
            self._html(HTML_PAGE)

        elif path == "/api/conversations":
            convs = [
                {**c, "bookmarked": c["id"] in STORE.bookmarks}
                for c in STORE.conversations
            ]
            self._json({"conversations": convs, "projects": STORE.projects})

        elif path.startswith("/api/conversation/"):
            conv_id = path.split("/")[-1]
            if conv_id not in STORE.by_id:
                self._json({"error": "Not found"}, 404)
                return
            filepath = STORE._file_map[conv_id]
            messages = parse_full_conversation(filepath)
            self._json({
                "metadata": {**STORE.by_id[conv_id], "bookmarked": conv_id in STORE.bookmarks},
                "messages": messages,
            })

        elif path.startswith("/api/export/"):
            conv_id = path.split("/")[-1]
            fmt = params.get("format", ["md"])[0]
            if conv_id not in STORE.by_id:
                self._json({"error": "Not found"}, 404)
                return
            filepath = STORE._file_map[conv_id]
            meta = STORE.by_id[conv_id]
            if fmt == "json":
                msgs = parse_full_conversation(filepath)
                content = json.dumps({"metadata": meta, "messages": msgs}, indent=2).encode("utf-8")
                self._download(content, f"{conv_id}.json", "application/json")
            else:
                md = export_as_markdown(filepath, meta)
                self._download(md.encode("utf-8"), f"{conv_id}.md", "text/markdown")

        elif path == "/api/export-all":
            fmt = params.get("format", ["md"])[0]
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for conv_id, filepath in STORE._file_map.items():
                    meta = STORE.by_id[conv_id]
                    try:
                        if fmt == "json":
                            msgs = parse_full_conversation(filepath)
                            content = json.dumps({"metadata": meta, "messages": msgs}, indent=2)
                            zf.writestr(f"{conv_id}.json", content)
                        else:
                            zf.writestr(f"{conv_id}.md", export_as_markdown(filepath, meta))
                    except Exception:
                        continue
            zip_bytes = buf.getvalue()
            self._download(zip_bytes, f"claude-conversations.zip", "application/zip")

        elif path == "/api/stats":
            self._json(STORE.get_stats())

        elif path == "/api/search":
            query = params.get("q", [""])[0].strip()
            if len(query) < 2:
                self._json({"results": [], "query": query})
                return
            results = STORE.search_content(query)
            self._json({"results": results, "query": query})

        elif path == "/api/bookmarks":
            bookmarked_ids = list(STORE.bookmarks.keys())
            convs = [
                {**STORE.by_id[bid], "bookmarked": True}
                for bid in bookmarked_ids
                if bid in STORE.by_id
            ]
            convs.sort(key=lambda c: c.get("last_timestamp") or "", reverse=True)
            self._json({"bookmarks": convs})

        elif path == "/api/status":
            self._json({
                "count": len(STORE.conversations),
                "last_scanned": STORE.last_scanned,
            })

        elif path == "/api/refresh":
            old_count = len(STORE.conversations)
            STORE.load()
            new_count = len(STORE.conversations)
            convs = [
                {**c, "bookmarked": c["id"] in STORE.bookmarks}
                for c in STORE.conversations
            ]
            self._json({
                "total": new_count,
                "new_count": new_count - old_count,
                "conversations": convs,
                "projects": STORE.projects,
            })

        elif path == "/api/update-check":
            if check_for_update_sync is not None:
                self._json(check_for_update_sync())
            else:
                self._json({"update_available": False})

        else:
            self._json({"error": "Not found"}, 404)

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            data = {}

        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/bookmarks":
            session_id = data.get("id", "").strip()
            bookmarked = data.get("bookmarked", True)
            if not session_id:
                self._json({"error": "Missing id"}, 400)
                return
            bookmarks = load_bookmarks()
            if bookmarked:
                bookmarks[session_id] = True
            else:
                bookmarks.pop(session_id, None)
            save_bookmarks(bookmarks)
            STORE.bookmarks = bookmarks
            self._json({"ok": True, "bookmarked": bookmarked})
        else:
            self._json({"error": "Not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

# ---------------------------------------------------------------------------
# Embedded Frontend
# ---------------------------------------------------------------------------

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Claude Conversations</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.1/marked.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg:            #080b10;
  --bg-surface:    #0f1318;
  --bg-raised:     #161b24;
  --bg-elevated:   #1c2333;
  --border:        #21262e;
  --border-subtle: #181c24;
  --text:          #e2e8f0;
  --text-muted:    #64748b;
  --text-faint:    #374151;
  --accent:        #8b5cf6;
  --accent-bright: #a78bfa;
  --accent-dim:    rgba(139, 92, 246, 0.12);
  --accent-glow:   rgba(139, 92, 246, 0.06);
  --indigo:        #6366f1;
  --indigo-dim:    rgba(99, 102, 241, 0.12);
  --green:         #22c55e;
  --green-dim:     rgba(34, 197, 94, 0.1);
  --yellow:        #eab308;
  --yellow-dim:    rgba(234, 179, 8, 0.1);
  --red:           #ef4444;
  --cyan:          #22d3ee;
  --user-bg:          rgba(56, 139, 253, 0.07);
  --user-border:      rgba(56, 139, 253, 0.22);
  --user-accent:      #3b82f6;
  --asst-bg:          rgba(139, 92, 246, 0.07);
  --asst-border:      rgba(139, 92, 246, 0.22);
  --asst-accent:      #8b5cf6;
  --thinking-bg:      rgba(245, 158, 11, 0.06);
  --thinking-border:  rgba(245, 158, 11, 0.25);
  --thinking-accent:  #f59e0b;
  --thinking-dim:     rgba(245, 158, 11, 0.12);
  --radius:        10px;
  --radius-sm:     6px;
  --radius-xs:     4px;
  --shadow:        0 4px 24px rgba(0,0,0,0.4);
  --shadow-sm:     0 2px 8px rgba(0,0,0,0.3);
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', sans-serif;
  background: var(--bg);
  color: var(--text);
  height: 100vh;
  overflow: hidden;
  font-size: 14px;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}

/* ── Layout ── */
.app { display: flex; height: 100vh; }

/* ── Sidebar ── */
.sidebar {
  width: 340px;
  min-width: 340px;
  background: var(--bg-surface);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  height: 100vh;
  position: relative;
}

.sidebar-header {
  padding: 14px 14px 10px;
  border-bottom: 1px solid var(--border-subtle);
  flex-shrink: 0;
}

.brand {
  display: flex;
  align-items: center;
  gap: 9px;
  margin-bottom: 12px;
}

.brand-icon {
  width: 30px;
  height: 30px;
  background: linear-gradient(135deg, var(--accent) 0%, #c084fc 100%);
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  flex-shrink: 0;
  box-shadow: 0 2px 8px rgba(139,92,246,0.35);
}

.brand-text { font-size: 14px; font-weight: 600; letter-spacing: -0.01em; }
.brand-sub { font-size: 11px; color: var(--text-muted); font-weight: 400; }

.brand-actions { margin-left: auto; display: flex; gap: 6px; }

.icon-btn {
  width: 28px;
  height: 28px;
  border: 1px solid var(--border);
  background: var(--bg-raised);
  color: var(--text-muted);
  border-radius: var(--radius-sm);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  transition: all 0.15s;
  flex-shrink: 0;
}
.icon-btn:hover { background: var(--bg-elevated); color: var(--text); border-color: var(--accent); }
.icon-btn.active { background: var(--accent-dim); color: var(--accent-bright); border-color: var(--accent); }

.tabs {
  display: flex;
  gap: 3px;
  background: var(--bg-raised);
  padding: 3px;
  border-radius: var(--radius-sm);
  margin-bottom: 10px;
}

.tab-btn {
  flex: 1;
  padding: 5px 8px;
  border: none;
  background: transparent;
  color: var(--text-muted);
  border-radius: 5px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  transition: all 0.15s;
  white-space: nowrap;
}
.tab-btn.active { background: var(--bg-elevated); color: var(--text); box-shadow: var(--shadow-sm); }
.tab-btn:hover:not(.active) { color: var(--text); }

.search-wrap { position: relative; margin-bottom: 8px; }

.search-icon {
  position: absolute;
  left: 9px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--text-muted);
  font-size: 13px;
  pointer-events: none;
}

.search-box {
  width: 100%;
  padding: 7px 32px 7px 30px;
  background: var(--bg-raised);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  font-size: 13px;
  outline: none;
  transition: border-color 0.15s, background 0.15s;
}
.search-box:focus { border-color: var(--accent); background: var(--bg-elevated); }
.search-box::placeholder { color: var(--text-faint); }

.search-mode-toggle {
  position: absolute;
  right: 6px;
  top: 50%;
  transform: translateY(-50%);
  padding: 2px 6px;
  font-size: 10px;
  font-weight: 600;
  border: 1px solid var(--border);
  background: var(--bg);
  color: var(--text-muted);
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s;
  letter-spacing: 0.02em;
}
.search-mode-toggle:hover { color: var(--accent); border-color: var(--accent); }
.search-mode-toggle.active { background: var(--accent-dim); color: var(--accent-bright); border-color: var(--accent); }

.filter-row { display: flex; gap: 6px; }

.filter-select {
  flex: 1;
  min-width: 0;
  padding: 5px 8px;
  background: var(--bg-raised);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  font-size: 12px;
  outline: none;
  cursor: pointer;
  transition: border-color 0.15s;
}
.filter-select:focus { border-color: var(--accent); }

/* ── Conversation list ── */
.conv-list { flex: 1; overflow-y: auto; padding: 6px; }

.conv-item {
  padding: 10px 11px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  border: 1px solid transparent;
  margin-bottom: 2px;
  transition: all 0.12s;
  position: relative;
}
.conv-item:hover { background: var(--bg-raised); border-color: var(--border); }
.conv-item.active { background: var(--accent-dim); border-color: rgba(139,92,246,0.3); }
.conv-item.active:hover { background: var(--accent-dim); }

.conv-item-top { display: flex; align-items: flex-start; gap: 6px; margin-bottom: 2px; }

.conv-project {
  font-size: 10px;
  font-weight: 600;
  color: var(--accent-bright);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  letter-spacing: 0.02em;
  text-transform: uppercase;
  flex: 1;
  min-width: 0;
}

.bookmark-btn {
  background: none;
  border: none;
  cursor: pointer;
  color: var(--text-faint);
  font-size: 13px;
  padding: 0;
  line-height: 1;
  flex-shrink: 0;
  transition: color 0.15s, transform 0.1s;
}
.bookmark-btn:hover { color: var(--yellow); transform: scale(1.15); }
.bookmark-btn.bookmarked { color: var(--yellow); }

.conv-title {
  font-size: 13px;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 3px;
  color: var(--text);
  line-height: 1.3;
}

.conv-preview {
  font-size: 12px;
  color: var(--text-muted);
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  line-height: 1.4;
  margin-bottom: 5px;
  word-break: break-word;
}

.conv-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--text-muted);
  flex-wrap: wrap;
}

.meta-sep { color: var(--text-faint); }

.cost-badge {
  color: var(--green);
  font-size: 10px;
  font-weight: 600;
  background: var(--green-dim);
  padding: 1px 5px;
  border-radius: 10px;
  border: 1px solid rgba(34,197,94,0.2);
}

.model-badge {
  color: var(--accent-bright);
  font-size: 10px;
  background: var(--accent-dim);
  padding: 1px 5px;
  border-radius: 10px;
  border: 1px solid rgba(139,92,246,0.2);
  max-width: 110px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.search-snippet {
  font-size: 11px;
  color: var(--text-muted);
  background: var(--bg-raised);
  border-left: 2px solid var(--accent);
  padding: 4px 8px;
  border-radius: 0 4px 4px 0;
  margin-top: 4px;
  font-style: italic;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

/* ── Refresh banner ── */
.refresh-banner {
  background: var(--indigo-dim);
  border-bottom: 1px solid rgba(99,102,241,0.3);
  padding: 7px 12px;
  font-size: 12px;
  display: none;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.refresh-banner.show { display: flex; }
.refresh-banner button {
  margin-left: auto;
  padding: 2px 10px;
  font-size: 11px;
  background: var(--indigo);
  border: none;
  color: white;
  border-radius: 4px;
  cursor: pointer;
  font-weight: 500;
}
.refresh-banner button:hover { opacity: 0.85; }

/* ── Main panel ── */
.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--bg);
  min-width: 0;
}

.main-header {
  border-bottom: 1px solid var(--border);
  background: var(--bg-surface);
  flex-shrink: 0;
}

.main-header-top {
  display: flex;
  align-items: center;
  padding: 10px 18px;
  gap: 10px;
  min-height: 50px;
}

.back-btn {
  display: none;
  padding: 5px 10px;
  border: 1px solid var(--border);
  background: var(--bg-raised);
  color: var(--text-muted);
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 12px;
  flex-shrink: 0;
  transition: all 0.15s;
}
.back-btn:hover { color: var(--text); border-color: var(--accent); }

.main-header-title {
  font-size: 14px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
  min-width: 0;
  letter-spacing: -0.01em;
}

.main-header-actions { display: flex; gap: 6px; flex-shrink: 0; }

.main-header-session {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 18px 8px;
  background: var(--bg-raised);
  border-top: 1px solid var(--border-subtle);
  font-size: 12px;
  flex-wrap: wrap;
}

.session-label { color: var(--text-muted); font-size: 11px; flex-shrink: 0; }

.session-id {
  font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
  font-size: 11.5px;
  color: var(--cyan);
  background: var(--bg);
  padding: 2px 8px;
  border-radius: var(--radius-xs);
  border: 1px solid var(--border);
  user-select: all;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
  max-width: 320px;
}

/* ── Buttons ── */
.btn {
  padding: 5px 12px;
  border: 1px solid var(--border);
  background: var(--bg-raised);
  color: var(--text);
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  transition: all 0.15s;
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.btn:hover { background: var(--bg-elevated); border-color: var(--accent); color: var(--accent-bright); }
.btn.btn-primary { background: var(--accent); border-color: var(--accent); color: white; }
.btn.btn-primary:hover { background: #7c3aed; border-color: #7c3aed; color: white; }
.btn.btn-sm { padding: 3px 9px; font-size: 11px; }
.btn.btn-icon { padding: 5px 8px; }
.btn.bookmarked-active { background: var(--yellow-dim); border-color: rgba(234,179,8,0.3); color: var(--yellow); }

/* ── Messages ── */
.messages-container {
  flex: 1;
  overflow-y: auto;
  padding: 24px 28px;
}

.message {
  margin-bottom: 20px;
  max-width: 860px;
  border-radius: var(--radius);
  border: 1px solid transparent;
  padding: 16px 20px;
}

.message.user {
  background: var(--user-bg);
  border-color: var(--user-border);
  border-left: 3px solid var(--user-accent);
}

.message.assistant {
  background: var(--asst-bg);
  border-color: var(--asst-border);
  border-left: 3px solid var(--asst-accent);
}

.message-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
  gap: 8px;
}

.message-role-wrap { display: flex; align-items: center; gap: 7px; }

.message-role {
  font-weight: 700;
  font-size: 10.5px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  padding: 2px 7px;
  border-radius: 4px;
}
.message.user .message-role { color: #60a5fa; background: rgba(56,139,253,0.15); }
.message.assistant .message-role { color: var(--accent-bright); background: var(--accent-dim); }

/* ── Thinking block ── */
.thinking-block {
  background: var(--thinking-bg);
  border: 1px solid var(--thinking-border);
  border-left: 3px solid var(--thinking-accent);
  border-radius: var(--radius-sm);
  margin: 8px 0;
}
.thinking-header {
  display: flex; align-items: center; gap: 7px;
  padding: 7px 12px; cursor: pointer; user-select: none;
}
.thinking-label {
  font-size: 10.5px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--thinking-accent);
  background: var(--thinking-dim); padding: 2px 7px; border-radius: 4px;
}
.thinking-arrow { color: var(--thinking-accent); font-size: 10px; transition: transform 0.15s; }
.thinking-arrow.open { transform: rotate(90deg); }
.thinking-body {
  padding: 0 12px 12px;
  font-size: 13px; line-height: 1.6; color: var(--text-muted);
  font-style: italic; white-space: pre-wrap; word-break: break-word;
}

/* ── Search highlight ── */
mark.search-highlight {
  background: rgba(251, 191, 36, 0.35);
  color: #fde68a;
  border-radius: 2px;
  padding: 0 2px;
  outline: 1px solid rgba(251, 191, 36, 0.5);
}

/* ── Color legend ── */
.conv-legend {
  display: flex; align-items: center; gap: 16px;
  padding: 7px 20px;
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border);
  font-size: 11px; color: var(--text-muted);
  flex-shrink: 0;
}
.legend-item { display: flex; align-items: center; gap: 5px; }
.legend-dot {
  width: 10px; height: 10px; border-radius: 2px; flex-shrink: 0;
}
.legend-dot.user     { background: var(--user-accent); }
.legend-dot.asst     { background: var(--asst-accent); }
.legend-dot.thinking { background: var(--thinking-accent); }

.message-model { font-size: 11px; color: var(--text-muted); }

.message-meta-right { display: flex; align-items: center; gap: 8px; }

.token-badge {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 2px 7px;
  background: var(--bg-raised);
  border: 1px solid var(--border);
  border-radius: 10px;
  font-size: 10.5px;
  color: var(--text-muted);
}

.msg-cost-badge {
  font-size: 10px;
  font-weight: 600;
  color: var(--green);
  background: var(--green-dim);
  padding: 2px 6px;
  border-radius: 10px;
  border: 1px solid rgba(34,197,94,0.2);
}

.message-time { color: var(--text-faint); font-size: 11px; }

/* ── Message body ── */
.message-body { font-size: 14px; line-height: 1.65; color: var(--text); }
.message-body p { margin-bottom: 10px; }
.message-body p:last-child { margin-bottom: 0; }

.message-body pre {
  background: #0d1117;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 14px 16px;
  overflow-x: auto;
  margin: 10px 0;
  font-size: 13px;
  position: relative;
}

.copy-btn {
  position: absolute;
  top: 7px;
  right: 7px;
  padding: 2px 8px;
  font-size: 10.5px;
  font-weight: 500;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-xs);
  color: var(--text-muted);
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s, color 0.15s, border-color 0.15s;
  font-family: inherit;
}
pre:hover .copy-btn { opacity: 1; }
.copy-btn:hover { color: var(--accent-bright); border-color: var(--accent); }
.copy-btn.copied { color: var(--green); border-color: var(--green); opacity: 1; }

.message-body code {
  font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
  background: var(--bg-elevated);
  padding: 2px 5px;
  border-radius: var(--radius-xs);
  font-size: 12.5px;
  border: 1px solid var(--border-subtle);
  color: var(--accent-bright);
}
.message-body pre code { background: none; padding: 0; border: none; font-size: 13px; color: inherit; }

.message-body ul, .message-body ol { margin: 8px 0 8px 22px; }
.message-body li { margin-bottom: 4px; }

.message-body blockquote {
  border-left: 3px solid var(--accent);
  padding-left: 14px;
  color: var(--text-muted);
  margin: 10px 0;
  font-style: italic;
}

.message-body h1, .message-body h2 { margin: 18px 0 8px; font-size: 1.15em; }
.message-body h3, .message-body h4 { margin: 14px 0 6px; font-size: 1.0em; }

.message-body table { border-collapse: collapse; margin: 10px 0; width: 100%; }
.message-body th, .message-body td { border: 1px solid var(--border); padding: 7px 12px; font-size: 13px; }
.message-body th { background: var(--bg-raised); font-weight: 600; }
.message-body tr:nth-child(even) td { background: rgba(255,255,255,0.015); }

.message-body a { color: var(--accent-bright); text-decoration: underline; text-underline-offset: 2px; }
.message-body a:hover { color: var(--cyan); }

/* ── Tool blocks ── */
.tool-block, .tool-result-block {
  margin: 10px 0;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  overflow: hidden;
}

.tool-header {
  padding: 8px 12px;
  background: var(--bg-raised);
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  font-weight: 500;
  user-select: none;
  transition: background 0.1s;
}
.tool-header:hover { background: var(--bg-elevated); }

.tool-arrow {
  font-size: 9px;
  color: var(--text-muted);
  transition: transform 0.15s;
  flex-shrink: 0;
}
.tool-arrow.open { transform: rotate(90deg); }

.tool-name {
  font-family: 'SF Mono', 'Fira Code', monospace;
  color: var(--accent-bright);
  font-size: 11.5px;
}

.tool-summary { color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 11px; }

.tool-body, .tool-result-body {
  padding: 10px 14px;
  border-top: 1px solid var(--border-subtle);
  font-size: 12px;
  max-height: 360px;
  overflow: auto;
}
.tool-body pre, .tool-result-body pre {
  margin: 0; white-space: pre-wrap; word-break: break-all; font-size: 12px;
  background: none; border: none; padding: 0;
}

.tool-result-header {
  padding: 7px 12px;
  background: var(--green-dim);
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11.5px;
  font-weight: 500;
  color: var(--green);
  user-select: none;
  transition: background 0.1s;
}
.tool-result-header:hover { background: rgba(34,197,94,0.15); }

/* ── Stats panel ── */
.stats-panel { padding: 28px; overflow-y: auto; height: 100%; }
.stats-panel h2 { font-size: 20px; font-weight: 700; margin-bottom: 22px; letter-spacing: -0.02em; }

.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin-bottom: 28px;
}

.stat-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 18px 20px;
  transition: border-color 0.15s;
}
.stat-card:hover { border-color: rgba(139,92,246,0.3); }

.stat-card-icon { font-size: 18px; margin-bottom: 8px; opacity: 0.7; }
.stat-value { font-size: 26px; font-weight: 700; letter-spacing: -0.03em; color: var(--accent-bright); }
.stat-card.cost .stat-value { color: var(--green); }
.stat-label { font-size: 11px; color: var(--text-muted); margin-top: 3px; font-weight: 500; letter-spacing: 0.02em; text-transform: uppercase; }

.stats-section { margin-bottom: 28px; }
.stats-section h3 { font-size: 13px; font-weight: 600; margin-bottom: 14px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; }

.stats-bar-chart { display: flex; flex-direction: column; gap: 8px; }

.bar-row { display: flex; align-items: center; gap: 12px; }
.bar-label { min-width: 180px; font-size: 12px; color: var(--text-muted); text-align: right; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.bar-track { flex: 1; height: 28px; background: var(--bg-raised); border-radius: var(--radius-sm); overflow: hidden; border: 1px solid var(--border-subtle); }
.bar-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--accent) 0%, #c084fc 100%);
  border-radius: var(--radius-sm);
  display: flex;
  align-items: center;
  padding: 0 10px;
  font-size: 11px;
  font-weight: 600;
  color: white;
  min-width: fit-content;
  transition: width 0.4s ease;
}

/* ── Heatmap ── */
.heatmap-container { overflow-x: auto; padding-bottom: 4px; }
.heatmap { display: flex; gap: 3px; }
.heatmap-week { display: flex; flex-direction: column; gap: 3px; }
.heatmap-cell {
  width: 12px; height: 12px; border-radius: 2px;
  background: var(--bg-raised);
  border: 1px solid var(--border-subtle);
  cursor: default;
  transition: transform 0.1s;
}
.heatmap-cell:hover { transform: scale(1.3); }
.heatmap-cell.l1 { background: rgba(139,92,246,0.2); border-color: rgba(139,92,246,0.3); }
.heatmap-cell.l2 { background: rgba(139,92,246,0.45); border-color: rgba(139,92,246,0.5); }
.heatmap-cell.l3 { background: rgba(139,92,246,0.7); border-color: rgba(139,92,246,0.75); }
.heatmap-cell.l4 { background: var(--accent); border-color: var(--accent); }
.heatmap-months { display: flex; gap: 3px; margin-bottom: 4px; padding-left: 0; }
.heatmap-month-label { font-size: 10px; color: var(--text-muted); }

/* ── Empty states ── */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--text-muted);
  font-size: 14px;
  gap: 10px;
  text-align: center;
  padding: 40px;
}
.empty-icon { font-size: 48px; opacity: 0.15; margin-bottom: 4px; }
.empty-title { font-size: 16px; font-weight: 600; color: var(--text); opacity: 0.5; }
.empty-hint { font-size: 12px; color: var(--text-faint); }

.first-run { gap: 12px; }
.first-run .empty-icon { opacity: 0.12; }
.setup-steps { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; max-width: 300px; }
.setup-step {
  display: flex; align-items: flex-start; gap: 10px;
  background: var(--bg-raised); border: 1px solid var(--border);
  border-radius: var(--radius-sm); padding: 10px 12px; text-align: left;
  font-size: 12px;
}
.step-num {
  width: 20px; height: 20px; border-radius: 50%;
  background: var(--accent-dim); color: var(--accent-bright);
  font-size: 10px; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}
.setup-step code { font-size: 11px; color: var(--accent-bright); background: var(--bg); padding: 1px 4px; border-radius: 3px; }

/* ── Loading ── */
.loading { display: flex; align-items: center; justify-content: center; padding: 40px; color: var(--text-muted); gap: 10px; }
.spinner { width: 18px; height: 18px; border: 2px solid var(--border); border-top-color: var(--accent); border-radius: 50%; animation: spin 0.55s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* ── Update banner ── */
.update-banner {
  background: linear-gradient(90deg, rgba(139,92,246,0.12) 0%, rgba(192,132,252,0.08) 100%);
  border-bottom: 1px solid rgba(139,92,246,0.25);
  color: var(--text);
  padding: 8px 16px;
  font-size: 12.5px;
  display: none;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.update-banner.show { display: flex; }
.update-banner code { background: var(--bg-elevated); padding: 1px 7px; border-radius: 4px; font-size: 11.5px; color: var(--accent-bright); }
.update-banner .dismiss-btn { background: none; border: none; color: var(--text-muted); cursor: pointer; font-size: 16px; padding: 0 4px; line-height: 1; flex-shrink: 0; }
.update-banner .dismiss-btn:hover { color: var(--text); }

/* ── Shortcuts hint ── */
.shortcuts-hint {
  padding: 6px 14px;
  border-top: 1px solid var(--border-subtle);
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  flex-shrink: 0;
}
.shortcut { font-size: 10px; color: var(--text-faint); }
.shortcut kbd {
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: 3px;
  padding: 1px 4px;
  font-size: 10px;
  font-family: inherit;
  color: var(--text-muted);
}

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 7px; height: 7px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: var(--text-faint); }

/* ── Responsive ── */
@media (max-width: 1024px) {
  .sidebar { width: 300px; min-width: 300px; }
  .message { max-width: 100%; }
}

@media (max-width: 768px) {
  .sidebar { width: 100%; min-width: 100%; }
  .main { display: none; }
  .app.conv-open .sidebar { display: none; }
  .app.conv-open .main { display: flex; }
  .back-btn { display: flex; }
  .messages-container { padding: 14px; }
  .message { padding: 12px 14px; }
  .stats-panel { padding: 16px; }
  .stats-grid { grid-template-columns: repeat(2, 1fr); gap: 10px; }
  .stat-value { font-size: 22px; }
  .bar-label { min-width: 100px; }
  .filter-row { flex-direction: column; }
}

@media (max-width: 480px) {
  .sidebar-header { padding: 10px; }
  .conv-list { padding: 4px; }
  .conv-item { padding: 9px 10px; }
  .stats-grid { grid-template-columns: 1fr 1fr; gap: 8px; }
}
</style>
</head>
<body>

<div class="update-banner" id="updateBanner">
  <span>&#10022; Update available! Run <code>pip install --upgrade claude-conversation-viewer</code></span>
  <button class="dismiss-btn" onclick="dismissUpdate()" title="Dismiss">&times;</button>
</div>

<div class="app" id="app">
  <!-- Sidebar -->
  <div class="sidebar">
    <div class="sidebar-header">
      <div class="brand">
        <div class="brand-icon">&#9670;</div>
        <div>
          <div class="brand-text">Claude Conversations</div>
        </div>
        <div class="brand-actions">
          <button class="icon-btn" id="refreshBtn" onclick="triggerRefresh()" title="Refresh conversations">&#8635;</button>
          <div class="icon-btn" style="position:relative; cursor:default;" title="Export all">
            <span style="font-size:11px;cursor:pointer;" onclick="showExportMenu(this)">&#8615;</span>
            <div id="exportMenu" style="display:none;position:absolute;top:30px;right:0;background:var(--bg-raised);border:1px solid var(--border);border-radius:var(--radius-sm);min-width:160px;z-index:100;overflow:hidden;">
              <div style="padding:7px 12px;font-size:12px;cursor:pointer;transition:background 0.1s;" onmouseover="this.style.background='var(--bg-elevated)'" onmouseout="this.style.background=''" onclick="exportAll('md')">Export all as .md (zip)</div>
              <div style="padding:7px 12px;font-size:12px;cursor:pointer;transition:background 0.1s;border-top:1px solid var(--border-subtle);" onmouseover="this.style.background='var(--bg-elevated)'" onmouseout="this.style.background=''" onclick="exportAll('json')">Export all as .json (zip)</div>
            </div>
          </div>
        </div>
      </div>
      <div class="tabs">
        <button class="tab-btn active" data-tab="conversations" onclick="switchTab('conversations')">Conversations</button>
        <button class="tab-btn" data-tab="bookmarked" onclick="switchTab('bookmarked')">&#9733; Saved</button>
        <button class="tab-btn" data-tab="stats" onclick="switchTab('stats')">Stats</button>
      </div>
      <div class="search-wrap" id="searchWrap">
        <span class="search-icon">&#9906;</span>
        <input type="text" class="search-box" id="searchBox" placeholder="Search conversations…" oninput="onSearchInput()" onkeydown="onSearchKey(event)">
        <button class="search-mode-toggle" id="searchModeBtn" onclick="toggleContentSearch()" title="Toggle content search">DEEP</button>
      </div>
      <div class="filter-row">
        <select class="filter-select" id="projectFilter" onchange="filterConversations()">
          <option value="">All projects</option>
        </select>
        <select class="filter-select" id="sortSelect" onchange="filterConversations()">
          <option value="newest">Newest</option>
          <option value="oldest">Oldest</option>
          <option value="most-messages">Most msgs</option>
          <option value="most-tokens">Most tokens</option>
          <option value="highest-cost">Highest cost</option>
        </select>
      </div>
    </div>
    <div class="refresh-banner" id="refreshBanner">
      <span id="refreshBannerText">New conversations available</span>
      <button onclick="applyRefresh()">Refresh</button>
    </div>
    <div class="conv-list" id="convList">
      <div class="loading"><div class="spinner"></div> Loading…</div>
    </div>
    <div class="shortcuts-hint">
      <span class="shortcut"><kbd>/</kbd> search</span>
      <span class="shortcut"><kbd>j</kbd><kbd>k</kbd> navigate</span>
      <span class="shortcut"><kbd>b</kbd> bookmark</span>
      <span class="shortcut"><kbd>↵</kbd> open</span>
    </div>
  </div>

  <!-- Main panel -->
  <div class="main" id="mainPanel">
    <div class="main-header" id="mainHeader">
      <div class="main-header-top">
        <button class="back-btn" onclick="goBack()">&#8592; Back</button>
        <span class="main-header-title" id="mainTitle">Select a conversation</span>
        <div class="main-header-actions" id="mainActions" style="display:none">
          <button class="btn btn-icon" id="bookmarkHeaderBtn" onclick="toggleBookmarkCurrent()" title="Bookmark">&#9733;</button>
          <button class="btn btn-sm" onclick="exportConversation('md')">&#8615; .md</button>
          <button class="btn btn-sm" onclick="exportConversation('json')">&#8615; .json</button>
        </div>
      </div>
      <div class="main-header-session" id="sessionBar" style="display:none">
        <span class="session-label">Session ID</span>
        <code class="session-id" id="sessionIdText"></code>
        <button class="btn btn-sm" onclick="copyResume()" id="copyBtn">Copy resume cmd</button>
      </div>
    </div>

    <div class="conv-legend" id="convLegend" style="display:none">
      <span style="color:var(--text-faint);font-weight:600;margin-right:4px;">KEY</span>
      <span class="legend-item"><span class="legend-dot user"></span> You</span>
      <span class="legend-item"><span class="legend-dot asst"></span> Claude</span>
      <span class="legend-item"><span class="legend-dot thinking"></span> Thinking</span>
    </div>

    <div class="messages-container" id="messagesContainer">
      <div class="empty-state">
        <div class="empty-icon">&#9670;</div>
        <div class="empty-title">No conversation selected</div>
        <div class="empty-hint">Use <kbd style="background:var(--bg-elevated);border:1px solid var(--border);border-radius:3px;padding:1px 5px;font-size:11px;">/</kbd> to search or click any conversation</div>
        <div style="margin-top:20px;padding:12px 18px;background:var(--bg-surface);border:1px solid var(--border);border-radius:var(--radius);font-size:12px;color:var(--text-muted);line-height:1.8;text-align:left;max-width:320px;">
          <div style="font-weight:600;color:var(--text);margin-bottom:6px;">How to restart</div>
          <div>Browser tab closed? <span style="color:var(--accent-bright)">Just reopen this tab</span> or visit <code style="background:var(--bg-elevated);padding:1px 5px;border-radius:3px;font-size:11px;color:var(--accent-bright)">http://127.0.0.1:5005</code></div>
          <div style="margin-top:4px;">Server stopped? Run <code style="background:var(--bg-elevated);padding:1px 5px;border-radius:3px;font-size:11px;color:var(--accent-bright)">ccv</code> in your terminal to restart.</div>
          <div style="margin-top:4px;">Auto-start on login: <code style="background:var(--bg-elevated);padding:1px 5px;border-radius:3px;font-size:11px;color:var(--accent-bright)">ccv --install</code></div>
        </div>
      </div>
    </div>

    <div class="stats-panel" id="statsPanel" style="display:none">
      <div class="loading"><div class="spinner"></div> Loading stats…</div>
    </div>
  </div>
</div>

<script>
// ── State ──
let allConversations = [];
let allProjects = [];
let currentConvId = null;
let currentTab = 'conversations';
let contentSearchMode = false;
let searchDebounce = null;
let refreshData = null;
let filteredList = [];
let selectedIdx = -1;

// ── Init ──
document.addEventListener('DOMContentLoaded', init);
document.addEventListener('keydown', handleGlobalKey);
document.addEventListener('click', handleGlobalClick);

async function init() {
  const res = await fetch('/api/conversations');
  const data = await res.json();
  allConversations = data.conversations || [];
  allProjects = data.projects || [];

  const projectDisplayNames = {};
  allConversations.forEach(c => {
    if (!projectDisplayNames[c.project])
      projectDisplayNames[c.project] = c.project_path || c.project;
  });

  const sel = document.getElementById('projectFilter');
  allProjects.forEach(p => {
    const opt = document.createElement('option');
    opt.value = p;
    opt.textContent = shortenPath(projectDisplayNames[p]) || p;
    sel.appendChild(opt);
  });

  renderConversationList(allConversations);

  if (!sessionStorage.getItem('update-dismissed'))
    checkForUpdate();

  // Auto-refresh: poll every 45 seconds
  setInterval(pollForNewConversations, 45000);

  // Init marked options
  if (typeof marked !== 'undefined') {
    marked.setOptions({ breaks: true, gfm: true });
  }
}

// ── Update check ──
async function checkForUpdate() {
  try {
    const r = await fetch('/api/update-check');
    const d = await r.json();
    if (d.update_available) document.getElementById('updateBanner').classList.add('show');
  } catch {}
}
function dismissUpdate() {
  document.getElementById('updateBanner').classList.remove('show');
  sessionStorage.setItem('update-dismissed', '1');
}

// ── Auto-refresh polling ──
async function pollForNewConversations() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    if (d.count > allConversations.length) {
      const diff = d.count - allConversations.length;
      const banner = document.getElementById('refreshBanner');
      document.getElementById('refreshBannerText').textContent =
        `${diff} new conversation${diff > 1 ? 's' : ''} available`;
      banner.classList.add('show');
    }
  } catch {}
}

async function triggerRefresh() {
  const btn = document.getElementById('refreshBtn');
  btn.style.opacity = '0.5';
  try {
    const r = await fetch('/api/refresh');
    const d = await r.json();
    allConversations = d.conversations || [];
    allProjects = d.projects || [];
    document.getElementById('refreshBanner').classList.remove('show');
    filterConversations();
  } catch {}
  btn.style.opacity = '1';
}

function applyRefresh() { triggerRefresh(); }

// ── Helpers ──
function shortenPath(p) {
  if (!p) return '';
  const parts = p.replace(/\\/g, '/').split('/').filter(Boolean);
  const home = parts.indexOf('Users') >= 0 ? parts.slice(parts.indexOf('Users') + 2) : parts;
  if (home.length === 0) return '~';
  if (home.length <= 2) return '~/' + home.join('/');
  return home.slice(-2).join('/');
}

function formatDate(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  const now = new Date();
  const diffDays = Math.floor((now - d) / 86400000);
  if (diffDays === 0) return 'Today ' + d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
  if (diffDays === 1) return 'Yesterday';
  if (diffDays < 7) return d.toLocaleDateString([], {weekday:'short'});
  return d.toLocaleDateString([], {month:'short', day:'numeric'});
}

function formatTokens(n) {
  if (n >= 1e6) return (n/1e6).toFixed(1)+'M';
  if (n >= 1e3) return (n/1e3).toFixed(0)+'K';
  return n.toString();
}

function formatCost(usd) {
  if (!usd || usd === 0) return '';
  if (usd < 0.005) return '<$0.01';
  if (usd < 1) return '$' + usd.toFixed(2);
  return '$' + usd.toFixed(2);
}

function cleanModelName(m) {
  if (!m) return '';
  // Strip trailing date like -20251001
  return m.replace(/-\d{8}$/, '');
}

function escapeHtml(s) {
  if (!s) return '';
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Tabs ──
function switchTab(tab) {
  currentTab = tab;
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));

  const header = document.getElementById('mainHeader');
  const msgs = document.getElementById('messagesContainer');
  const stats = document.getElementById('statsPanel');
  const searchWrap = document.getElementById('searchWrap');
  const filterRow = document.querySelector('.filter-row');

  if (tab === 'stats') {
    header.style.display = 'none';
    msgs.style.display = 'none';
    stats.style.display = 'block';
    loadStats();
  } else {
    header.style.display = 'block';
    msgs.style.display = 'block';
    stats.style.display = 'none';
    if (!currentConvId) document.getElementById('sessionBar').style.display = 'none';
  }

  if (tab === 'bookmarked') {
    loadBookmarked();
  } else if (tab === 'conversations') {
    filterConversations();
  }
}

// ── Search ──
function onSearchInput() {
  if (currentTab === 'stats') return;
  clearTimeout(searchDebounce);
  if (contentSearchMode) {
    searchDebounce = setTimeout(runContentSearch, 400);
  } else {
    filterConversations();
  }
}

function onSearchKey(e) {
  if (e.key === 'Escape') { document.getElementById('searchBox').value = ''; filterConversations(); }
  if (e.key === 'ArrowDown') { e.preventDefault(); moveSel(1); }
  if (e.key === 'ArrowUp') { e.preventDefault(); moveSel(-1); }
  if (e.key === 'Enter') { e.preventDefault(); openSelected(); }
}

function toggleContentSearch() {
  contentSearchMode = !contentSearchMode;
  const btn = document.getElementById('searchModeBtn');
  btn.classList.toggle('active', contentSearchMode);
  btn.title = contentSearchMode ? 'Content search active — click to disable' : 'Click for deep content search';
  if (!contentSearchMode) filterConversations();
  else if (document.getElementById('searchBox').value.length >= 2) runContentSearch();
}

async function runContentSearch() {
  const q = document.getElementById('searchBox').value.trim();
  if (q.length < 2) { filterConversations(); return; }
  const container = document.getElementById('convList');
  container.innerHTML = '<div class="loading"><div class="spinner"></div> Searching content…</div>';
  try {
    const r = await fetch('/api/search?q=' + encodeURIComponent(q));
    const d = await r.json();
    renderSearchResults(d.results || [], q);
  } catch {
    filterConversations();
  }
}

function renderSearchResults(results, query) {
  const container = document.getElementById('convList');
  if (results.length === 0) {
    container.innerHTML = '<div class="empty-state"><div class="empty-icon" style="font-size:32px">&#128269;</div><div class="empty-title">No matches found</div><div class="empty-hint">Try different keywords</div></div>';
    return;
  }
  container.innerHTML = results.map((r, i) => `
    <div class="conv-item ${r.id === currentConvId ? 'active' : ''}" data-id="${escapeHtml(r.id)}" data-idx="${i}" onclick="loadConversation('${escapeHtml(r.id)}', ${JSON.stringify(query)})">
      <div class="conv-item-top">
        <span class="conv-project">${escapeHtml(shortenPath(r.project_path) || r.project_path)}</span>
        <button class="bookmark-btn ${r.bookmarked ? 'bookmarked' : ''}" onclick="event.stopPropagation();toggleBookmark('${escapeHtml(r.id)}',this)" title="Bookmark">&#9733;</button>
      </div>
      <div class="conv-title">${escapeHtml(r.title)}</div>
      ${r.snippet ? `<div class="search-snippet">${escapeHtml(r.snippet)}</div>` : ''}
      <div class="conv-meta">
        <span>${formatDate(r.first_timestamp)}</span>
      </div>
    </div>
  `).join('');
  filteredList = results.map(r => r.id);
}

// ── Filter ──
function filterConversations() {
  if (currentTab === 'bookmarked') return;
  const query = document.getElementById('searchBox').value.toLowerCase();
  const project = document.getElementById('projectFilter').value;
  const sort = document.getElementById('sortSelect').value;

  let list = allConversations;
  if (project) list = list.filter(c => c.project === project);
  if (query) {
    list = list.filter(c =>
      (c.title || '').toLowerCase().includes(query) ||
      (c.preview || '').toLowerCase().includes(query) ||
      (c.project_path || '').toLowerCase().includes(query) ||
      (c.models || []).some(m => m.toLowerCase().includes(query))
    );
  }

  if (sort === 'newest')       list = [...list].sort((a,b) => (b.last_timestamp||'').localeCompare(a.last_timestamp||''));
  else if (sort === 'oldest')  list = [...list].sort((a,b) => (a.first_timestamp||'').localeCompare(b.first_timestamp||''));
  else if (sort === 'most-messages') list = [...list].sort((a,b) => b.total_messages - a.total_messages);
  else if (sort === 'most-tokens')   list = [...list].sort((a,b) => (b.total_input_tokens+b.total_output_tokens)-(a.total_input_tokens+a.total_output_tokens));
  else if (sort === 'highest-cost')  list = [...list].sort((a,b) => (b.estimated_cost_usd||0)-(a.estimated_cost_usd||0));

  filteredList = list.map(c => c.id);
  renderConversationList(list);
}

// ── Bookmarks ──
async function loadBookmarked() {
  const container = document.getElementById('convList');
  container.innerHTML = '<div class="loading"><div class="spinner"></div> Loading…</div>';
  try {
    const r = await fetch('/api/bookmarks');
    const d = await r.json();
    const bookmarks = d.bookmarks || [];
    filteredList = bookmarks.map(c => c.id);
    if (bookmarks.length === 0) {
      container.innerHTML = '<div class="empty-state"><div class="empty-icon" style="font-size:36px">&#9733;</div><div class="empty-title">No saved conversations</div><div class="empty-hint">Click &#9733; on any conversation to save it</div></div>';
    } else {
      renderConversationList(bookmarks);
    }
  } catch {
    container.innerHTML = '<div class="empty-state"><div>Error loading bookmarks</div></div>';
  }
}

async function toggleBookmark(id, btn) {
  const isBookmarked = btn.classList.contains('bookmarked');
  const newState = !isBookmarked;
  try {
    await fetch('/api/bookmarks', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id, bookmarked: newState}),
    });
    btn.classList.toggle('bookmarked', newState);
    // Update allConversations
    const conv = allConversations.find(c => c.id === id);
    if (conv) conv.bookmarked = newState;
    // Update header btn
    if (id === currentConvId) {
      updateBookmarkHeaderBtn(newState);
    }
    if (currentTab === 'bookmarked') loadBookmarked();
  } catch {}
}

function updateBookmarkHeaderBtn(bookmarked) {
  const btn = document.getElementById('bookmarkHeaderBtn');
  btn.classList.toggle('bookmarked-active', bookmarked);
  btn.title = bookmarked ? 'Remove bookmark' : 'Bookmark this conversation';
}

async function toggleBookmarkCurrent() {
  if (!currentConvId) return;
  const conv = allConversations.find(c => c.id === currentConvId);
  const isBookmarked = conv ? conv.bookmarked : false;
  const newState = !isBookmarked;
  try {
    await fetch('/api/bookmarks', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: currentConvId, bookmarked: newState}),
    });
    if (conv) conv.bookmarked = newState;
    updateBookmarkHeaderBtn(newState);
    // Update sidebar btn
    const sideBtn = document.querySelector(`.conv-item[data-id="${currentConvId}"] .bookmark-btn`);
    if (sideBtn) sideBtn.classList.toggle('bookmarked', newState);
  } catch {}
}

// ── Render list ──
function renderConversationList(convs) {
  const container = document.getElementById('convList');
  if (convs.length === 0) {
    const hasAny = allConversations.length > 0;
    if (!hasAny) {
      container.innerHTML = `
        <div class="empty-state first-run">
          <div class="empty-icon">&#9670;</div>
          <div class="empty-title">No conversations yet</div>
          <div class="setup-steps">
            <div class="setup-step"><div class="step-num">1</div><div>Install Claude Code: <code>npm i -g @anthropic-ai/claude-code</code></div></div>
            <div class="setup-step"><div class="step-num">2</div><div>Start a session: <code>claude</code> in any directory</div></div>
            <div class="setup-step"><div class="step-num">3</div><div>Reload this page to browse your history</div></div>
          </div>
        </div>`;
    } else {
      container.innerHTML = '<div class="empty-state"><div class="empty-icon" style="font-size:32px">&#128269;</div><div class="empty-title">No matches</div><div class="empty-hint">Try a different search or filter</div></div>';
    }
    return;
  }

  filteredList = convs.map(c => c.id);

  container.innerHTML = convs.map((c, i) => {
    const cost = formatCost(c.estimated_cost_usd);
    const model = c.models && c.models.length ? cleanModelName(c.models[0]) : '';
    const previewText = c.preview && c.preview !== c.title ? c.preview.slice(c.title ? c.title.length : 0, 180).trim() : '';
    const totalTok = c.total_input_tokens + c.total_output_tokens;
    return `
    <div class="conv-item ${c.id === currentConvId ? 'active' : ''}" data-id="${escapeHtml(c.id)}" data-idx="${i}" onclick="loadConversation('${escapeHtml(c.id)}')">
      <div class="conv-item-top">
        <span class="conv-project" title="${escapeHtml(c.project_path || '')}">${escapeHtml(shortenPath(c.project_path) || c.project)}</span>
        <button class="bookmark-btn ${c.bookmarked ? 'bookmarked' : ''}" onclick="event.stopPropagation();toggleBookmark('${escapeHtml(c.id)}',this)" title="${c.bookmarked ? 'Remove bookmark' : 'Bookmark'}">&#9733;</button>
      </div>
      <div class="conv-title">${escapeHtml(c.title)}</div>
      ${previewText ? `<div class="conv-preview">${escapeHtml(previewText)}</div>` : ''}
      <div class="conv-meta">
        <span>${formatDate(c.first_timestamp)}</span>
        <span class="meta-sep">·</span>
        <span>${c.total_messages} msgs</span>
        ${cost ? `<span class="cost-badge">${escapeHtml(cost)}</span>` : ''}
        ${model ? `<span class="model-badge" title="${escapeHtml(c.models[0])}">${escapeHtml(model)}</span>` : ''}
      </div>
    </div>`;
  }).join('');

  selectedIdx = -1;
}

// ── Keyboard navigation ──
function moveSel(dir) {
  if (filteredList.length === 0) return;
  selectedIdx = Math.max(0, Math.min(filteredList.length - 1, selectedIdx + dir));
  document.querySelectorAll('.conv-item').forEach((el, i) => {
    el.classList.toggle('active', i === selectedIdx && el.dataset.id !== currentConvId);
    if (i === selectedIdx) el.scrollIntoView({block:'nearest'});
  });
}

function openSelected() {
  if (selectedIdx >= 0 && filteredList[selectedIdx]) {
    loadConversation(filteredList[selectedIdx]);
  }
}

function handleGlobalKey(e) {
  const tag = document.activeElement.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA') return;

  if (e.key === '/' || (e.key === 'k' && (e.metaKey || e.ctrlKey))) {
    e.preventDefault();
    document.getElementById('searchBox').focus();
    return;
  }
  if (e.key === 'j' || e.key === 'ArrowDown') { e.preventDefault(); moveSel(1); }
  if (e.key === 'k' || e.key === 'ArrowUp')   { e.preventDefault(); moveSel(-1); }
  if (e.key === 'Enter') openSelected();
  if (e.key === 'Escape') goBack();
  if (e.key === 'b' && currentConvId) toggleBookmarkCurrent();
}

function handleGlobalClick(e) {
  const menu = document.getElementById('exportMenu');
  if (menu && menu.style.display !== 'none' && !menu.contains(e.target) && !e.target.closest('.icon-btn')) {
    menu.style.display = 'none';
  }
}

function showExportMenu(el) {
  const menu = document.getElementById('exportMenu');
  menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
}

// ── Load conversation ──
async function loadConversation(id, searchQuery = null) {
  currentConvId = id;
  document.getElementById('app').classList.add('conv-open');
  document.getElementById('convLegend').style.display = 'flex';
  selectedIdx = -1;

  document.querySelectorAll('.conv-item').forEach(el => {
    el.classList.toggle('active', el.dataset.id === id);
  });

  const container = document.getElementById('messagesContainer');
  container.innerHTML = '<div class="loading"><div class="spinner"></div> Loading…</div>';
  document.getElementById('mainActions').style.display = 'flex';

  const res = await fetch(`/api/conversation/${id}`);
  const data = await res.json();
  const meta = data.metadata;
  const msgs = data.messages;

  document.getElementById('mainTitle').textContent = meta.title;
  document.getElementById('sessionBar').style.display = 'flex';
  document.getElementById('sessionIdText').textContent = meta.id;
  updateBookmarkHeaderBtn(meta.bookmarked);

  if (msgs.length === 0) {
    container.innerHTML = '<div class="empty-state"><div class="empty-icon">&#9670;</div><div class="empty-title">No messages</div></div>';
    return;
  }

  container.innerHTML = msgs.map(renderMessage).join('');

  if (typeof hljs !== 'undefined') {
    container.querySelectorAll('pre code').forEach(el => {
      try { hljs.highlightElement(el); } catch {}
    });
  }

  addCopyButtons(container);

  if (searchQuery) {
    highlightAndScroll(container, searchQuery);
  } else {
    container.scrollTop = 0;
  }
}

function highlightAndScroll(container, query) {
  if (!query || query.length < 2) { container.scrollTop = 0; return; }
  const lq = query.toLowerCase();

  function walk(node) {
    if (node.nodeType === 3) {
      const text = node.textContent;
      const idx = text.toLowerCase().indexOf(lq);
      if (idx === -1) return;
      const before = document.createTextNode(text.slice(0, idx));
      const mark = document.createElement('mark');
      mark.className = 'search-highlight';
      mark.textContent = text.slice(idx, idx + query.length);
      const after = document.createTextNode(text.slice(idx + query.length));
      const p = node.parentNode;
      p.replaceChild(after, node);
      p.insertBefore(mark, after);
      p.insertBefore(before, mark);
    } else if (node.nodeType === 1 && !['SCRIPT','STYLE','PRE','CODE'].includes(node.tagName)) {
      Array.from(node.childNodes).forEach(walk);
    }
  }
  walk(container);

  const first = container.querySelector('mark.search-highlight');
  if (first) first.scrollIntoView({ behavior: 'smooth', block: 'center' });
  else container.scrollTop = 0;
}

// ── Render message ──
function renderMessage(msg) {
  const role = msg.role;
  const time = msg.timestamp ? new Date(msg.timestamp).toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '';
  const content = msg.content || [];

  let tokBadge = '';
  let costBadge = '';
  let modelLabel = '';

  if (role === 'assistant') {
    if (msg.usage) {
      const u = msg.usage;
      const total = u.input_tokens + u.output_tokens;
      if (total > 0) {
        tokBadge = `<span class="token-badge">&#8595;${formatTokens(u.input_tokens)} &#8593;${formatTokens(u.output_tokens)}</span>`;
      }
      if (u.cache_read > 0) {
        tokBadge += ` <span class="token-badge" style="color:var(--cyan)">&#9400; ${formatTokens(u.cache_read)}</span>`;
      }
    }
    if (msg.estimated_cost_usd > 0.0001) {
      costBadge = `<span class="msg-cost-badge">${formatCost(msg.estimated_cost_usd)}</span>`;
    }
    if (msg.model) {
      modelLabel = `<span class="message-model">${escapeHtml(cleanModelName(msg.model))}</span>`;
    }
  }

  const bodyHtml = content.map(block => {
    if (block.type === 'text') return renderMarkdown(block.text);
    if (block.type === 'thinking') return renderThinking(block);
    if (block.type === 'tool_use') return renderToolUse(block);
    if (block.type === 'tool_result') return renderToolResult(block);
    return '';
  }).join('');

  return `
    <div class="message ${role}">
      <div class="message-header">
        <div class="message-role-wrap">
          <span class="message-role">${role}</span>
          ${modelLabel}
        </div>
        <div class="message-meta-right">
          ${tokBadge}
          ${costBadge}
          <span class="message-time">${escapeHtml(time)}</span>
        </div>
      </div>
      <div class="message-body">${bodyHtml}</div>
    </div>`;
}

function renderThinking(block) {
  const id = 'think-' + Math.random().toString(36).substr(2, 8);
  return `
    <div class="thinking-block">
      <div class="thinking-header" onclick="toggleBlock('${id}')">
        <span class="thinking-arrow" id="${id}-arrow">&#9654;</span>
        <span class="thinking-label">Thinking</span>
        <span style="font-size:11px;color:var(--text-faint);margin-left:4px;">Claude's internal reasoning</span>
      </div>
      <div class="thinking-body" id="${id}" style="display:none">${escapeHtml(block.text)}</div>
    </div>`;
}

function renderToolUse(block) {
  const id = 'tool-' + Math.random().toString(36).substr(2,8);
  let summary = '';
  const inp = block.input || {};
  if (block.name === 'Bash' && inp.command) summary = escapeHtml(inp.command.substring(0,90));
  else if ((block.name === 'Read' || block.name === 'Write' || block.name === 'Edit') && inp.file_path) summary = escapeHtml(inp.file_path);
  else if (block.name === 'Grep' && inp.pattern) summary = escapeHtml(inp.pattern);
  else if (block.name === 'Glob' && inp.pattern) summary = escapeHtml(inp.pattern);

  return `
    <div class="tool-block">
      <div class="tool-header" onclick="toggleBlock('${id}')">
        <span class="tool-arrow" id="${id}-arrow">&#9654;</span>
        <span class="tool-name">${escapeHtml(block.name)}</span>
        ${summary ? `<span class="tool-summary">${summary}</span>` : ''}
      </div>
      <div class="tool-body" id="${id}" style="display:none">
        <pre>${escapeHtml(JSON.stringify(block.input, null, 2).substring(0, 5000))}</pre>
      </div>
    </div>`;
}

function renderToolResult(block) {
  const id = 'res-' + Math.random().toString(36).substr(2,8);
  const content = block.content || '(empty)';
  return `
    <div class="tool-result-block">
      <div class="tool-result-header" onclick="toggleBlock('${id}')">
        <span class="tool-arrow" id="${id}-arrow">&#9654;</span>
        Tool Result
      </div>
      <div class="tool-result-body" id="${id}" style="display:none">
        <pre>${escapeHtml(content)}</pre>
      </div>
    </div>`;
}

function toggleBlock(id) {
  const el = document.getElementById(id);
  const arrow = document.getElementById(id + '-arrow');
  const open = el.style.display === 'none';
  el.style.display = open ? 'block' : 'none';
  arrow.classList.toggle('open', open);
}

function addCopyButtons(container) {
  container.querySelectorAll('pre').forEach(pre => {
    if (pre.querySelector('.copy-btn')) return;
    const btn = document.createElement('button');
    btn.className = 'copy-btn';
    btn.textContent = 'Copy';
    btn.addEventListener('click', () => {
      const code = pre.querySelector('code');
      const text = code ? code.textContent : pre.textContent.replace('Copy', '').trim();
      navigator.clipboard.writeText(text).then(() => {
        btn.textContent = 'Copied!';
        btn.classList.add('copied');
        setTimeout(() => { btn.textContent = 'Copy'; btn.classList.remove('copied'); }, 2000);
      });
    });
    pre.appendChild(btn);
  });
}

function renderMarkdown(text) {
  if (typeof marked !== 'undefined') {
    try { return marked.parse(text); } catch {}
  }
  return '<p>' + escapeHtml(text).replace(/\n/g,'<br>') + '</p>';
}

// ── Navigation ──
function goBack() {
  document.getElementById('app').classList.remove('conv-open');
  currentConvId = null;
  document.getElementById('mainActions').style.display = 'none';
  document.getElementById('sessionBar').style.display = 'none';
  document.getElementById('mainTitle').textContent = 'Select a conversation';
  document.getElementById('messagesContainer').innerHTML = `
    <div class="empty-state">
      <div class="empty-icon">&#9670;</div>
      <div class="empty-title">No conversation selected</div>
      <div class="empty-hint">Use <kbd style="background:var(--bg-elevated);border:1px solid var(--border);border-radius:3px;padding:1px 5px;font-size:11px;">/</kbd> to search</div>
    </div>`;
}

function copyResume() {
  if (!currentConvId) return;
  navigator.clipboard.writeText(`claude --resume ${currentConvId}`).then(() => {
    const btn = document.getElementById('copyBtn');
    btn.textContent = 'Copied!';
    setTimeout(() => { btn.textContent = 'Copy resume cmd'; }, 2000);
  });
}

function exportConversation(fmt) {
  if (!currentConvId) return;
  window.open(`/api/export/${currentConvId}?format=${fmt}`, '_blank');
}

function exportAll(fmt) {
  document.getElementById('exportMenu').style.display = 'none';
  window.open(`/api/export-all?format=${fmt}`, '_blank');
}

// ── Stats ──
async function loadStats() {
  const panel = document.getElementById('statsPanel');
  panel.innerHTML = '<div class="loading"><div class="spinner"></div> Loading stats…</div>';

  const res = await fetch('/api/stats');
  const s = await res.json();

  const totalCost = formatCost(s.total_cost_usd);
  const heatmapHtml = buildHeatmap(s.daily_activity || {});

  const modelBars = Object.entries(s.model_usage || {}).map(([m, cnt]) => {
    const max = Math.max(...Object.values(s.model_usage));
    const pct = (cnt / max * 100).toFixed(0);
    return `<div class="bar-row">
      <div class="bar-label" title="${escapeHtml(m)}">${escapeHtml(cleanModelName(m))}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${pct}%">${cnt}</div></div>
    </div>`;
  }).join('');

  const projectBars = Object.entries(s.project_counts || {}).slice(0,10).map(([p, cnt]) => {
    const max = Math.max(...Object.values(s.project_counts));
    const pct = (cnt / max * 100).toFixed(0);
    return `<div class="bar-row">
      <div class="bar-label" title="${escapeHtml(p)}">${escapeHtml(shortenPath(p))}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${pct}%">${cnt}</div></div>
    </div>`;
  }).join('');

  panel.innerHTML = `
    <h2>Usage Statistics</h2>
    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-card-icon">&#128172;</div>
        <div class="stat-value">${s.total_conversations}</div>
        <div class="stat-label">Conversations</div>
      </div>
      <div class="stat-card">
        <div class="stat-card-icon">&#128193;</div>
        <div class="stat-value">${s.total_projects}</div>
        <div class="stat-label">Projects</div>
      </div>
      <div class="stat-card">
        <div class="stat-card-icon">&#128172;</div>
        <div class="stat-value">${formatTokens(s.total_messages)}</div>
        <div class="stat-label">Messages</div>
      </div>
      <div class="stat-card cost">
        <div class="stat-card-icon">&#128178;</div>
        <div class="stat-value">${totalCost || '$0.00'}</div>
        <div class="stat-label">Est. Total Cost</div>
      </div>
      <div class="stat-card">
        <div class="stat-card-icon">&#8595;</div>
        <div class="stat-value">${formatTokens(s.total_input_tokens)}</div>
        <div class="stat-label">Input Tokens</div>
      </div>
      <div class="stat-card">
        <div class="stat-card-icon">&#8593;</div>
        <div class="stat-value">${formatTokens(s.total_output_tokens)}</div>
        <div class="stat-label">Output Tokens</div>
      </div>
      <div class="stat-card">
        <div class="stat-card-icon">&#9400;</div>
        <div class="stat-value">${formatTokens(s.total_cache_read_tokens)}</div>
        <div class="stat-label">Cache Read Tokens</div>
      </div>
      <div class="stat-card">
        <div class="stat-card-icon">&#128190;</div>
        <div class="stat-value">${formatTokens(s.total_cache_creation_tokens)}</div>
        <div class="stat-label">Cache Created</div>
      </div>
    </div>

    <div class="stats-section">
      <h3>Activity — last 52 weeks</h3>
      <div class="heatmap-container">${heatmapHtml}</div>
    </div>

    <div class="stats-section">
      <h3>Model Usage</h3>
      <div class="stats-bar-chart">${modelBars || '<div style="color:var(--text-muted)">No data</div>'}</div>
    </div>

    <div class="stats-section">
      <h3>Top Projects</h3>
      <div class="stats-bar-chart">${projectBars || '<div style="color:var(--text-muted)">No data</div>'}</div>
    </div>`;
}

function buildHeatmap(daily) {
  const today = new Date();
  const totalDays = 364;
  const cells = [];
  for (let i = totalDays; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(today.getDate() - i);
    const key = d.toISOString().split('T')[0];
    cells.push({date: key, count: daily[key] || 0});
  }

  const max = Math.max(...cells.map(c => c.count), 1);
  const weeks = [];
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i+7));

  const weeksHtml = weeks.map(week =>
    `<div class="heatmap-week">${week.map(day => {
      const lvl = day.count === 0 ? 0 : Math.min(4, Math.ceil(day.count / max * 4));
      return `<div class="heatmap-cell${lvl > 0 ? ' l'+lvl : ''}" title="${escapeHtml(day.date)}: ${day.count} conversation${day.count !== 1 ? 's' : ''}"></div>`;
    }).join('')}</div>`
  ).join('');

  const legend = `<div style="display:flex;align-items:center;gap:4px;margin-top:8px;font-size:11px;color:var(--text-muted);">
    Less <div class="heatmap-cell" style="display:inline-block"></div>
    <div class="heatmap-cell l1" style="display:inline-block"></div>
    <div class="heatmap-cell l2" style="display:inline-block"></div>
    <div class="heatmap-cell l3" style="display:inline-block"></div>
    <div class="heatmap-cell l4" style="display:inline-block"></div> More
  </div>`;

  return `<div class="heatmap">${weeksHtml}</div>${legend}`;
}
</script>
</body>
</html>"""

# ---------------------------------------------------------------------------
# Service management
# ---------------------------------------------------------------------------

def _get_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "com.claude-conversation-viewer.plist"


def install_service(port: int):
    if platform.system() != "Darwin":
        print("[ERROR] --install is macOS-only. For Linux use --install-systemd.")
        return
    plist_path = _get_plist_path()
    python = sys.executable
    script = os.path.abspath(__file__)
    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
    <key>Label</key><string>com.claude-conversation-viewer</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string><string>{script}</string>
        <string>--port</string><string>{port}</string><string>--no-open</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><false/>
    <key>StandardOutPath</key><string>/tmp/claude-conversation-viewer.log</string>
    <key>StandardErrorPath</key><string>/tmp/claude-conversation-viewer.log</string>
</dict></plist>"""
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(plist_content)
    os.system(f"launchctl unload {plist_path} 2>/dev/null")
    os.system(f"launchctl load {plist_path}")
    print(f"\n  Service installed and started at http://127.0.0.1:{port}")
    print(f"  Plist: {plist_path}")
    print(f"  Log:   /tmp/claude-conversation-viewer.log\n")


def uninstall_service():
    if platform.system() != "Darwin":
        print("[INFO] For Linux, remove the systemd unit manually.")
        return
    plist_path = _get_plist_path()
    if plist_path.exists():
        os.system(f"launchctl unload {plist_path} 2>/dev/null")
        plist_path.unlink()
        print("\n  Service stopped and removed.\n")
    else:
        print("\n  No service installed.\n")


def install_systemd_service(port: int):
    service_dir = Path.home() / ".config" / "systemd" / "user"
    service_dir.mkdir(parents=True, exist_ok=True)
    service_path = service_dir / "claude-conversation-viewer.service"
    python = sys.executable
    service_content = f"""[Unit]
Description=Claude Code Conversation Viewer
After=network.target

[Service]
ExecStart={python} -m claude_conversation_viewer.web --port {port} --no-open
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""
    service_path.write_text(service_content)
    print(f"\n  Systemd service written to: {service_path}")
    print(f"\n  Enable and start with:")
    print(f"    systemctl --user daemon-reload")
    print(f"    systemctl --user enable claude-conversation-viewer")
    print(f"    systemctl --user start claude-conversation-viewer\n")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Claude Code Conversation Viewer")
    parser.add_argument("--port", type=int, default=5005)
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--install", action="store_true", help="Install macOS LaunchAgent")
    parser.add_argument("--uninstall", action="store_true", help="Remove macOS LaunchAgent")
    parser.add_argument("--install-systemd", action="store_true", help="Install Linux systemd user service")
    parser.add_argument("--update", action="store_true", help="Update to latest version from PyPI")
    args = parser.parse_args()

    if args.update:
        import subprocess
        print("Updating claude-conversation-viewer...")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "claude-conversation-viewer"]
        )
        sys.exit(result.returncode)

    if args.uninstall:
        uninstall_service()
        return

    if args.install:
        install_service(args.port)
        return

    if args.install_systemd:
        install_systemd_service(args.port)
        return

    STORE.load()

    port = args.port
    try:
        server = HTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        # Port busy — find the next free one automatically
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        print(f"\n  Port {args.port} is already in use — switching to port {port}")
        server = HTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"

    print(f"\n  Claude Code Conversation Viewer  v{__version__}")
    print(f"  ═══════════════════════════════════════")
    print(f"  {len(STORE.conversations)} conversations · {len(STORE.projects)} projects")
    print(f"  Running at: {url}")
    print()
    print(f"  Browser closed?  Visit {url} to reopen")
    print(f"  Server stopped?  Run 'ccv' to restart")
    print(f"  Stop server:     Press Ctrl+C")
    print(f"  Tip: 'ccv --install' (macOS) or 'ccv --install-systemd' (Linux) to auto-start\n")

    if not args.no_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
