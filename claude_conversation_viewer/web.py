#!/usr/bin/env python3
"""Claude Code Conversation Viewer - Web UI.

Thin orchestration layer. The heavy lifting lives in sibling modules:

* ``pricing``   — model pricing table + cost estimation
* ``classifier`` — 13-category deterministic task classifier
* ``parser``    — JSONL parsing (metadata, per-turn data, full messages)
* ``cache``     — mtime-keyed metadata cache + bookmarks + plans
* ``store``     — :class:`ConversationStore`, the in-memory session index
* ``dashboard`` — aggregator + optimize + compare + yield + plans + export
"""

from __future__ import annotations

import argparse
import io
import json
import os
import platform
import sys
import threading
import time
import webbrowser
import zipfile
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse, parse_qs

try:
    from claude_conversation_viewer.update_checker import check_for_update_sync
except ImportError:
    check_for_update_sync = None

try:
    from claude_conversation_viewer import __version__
except ImportError:
    __version__ = "unknown"

# ── Module re-exports (tests import these from here) ─────────────────────────
from .pricing import MODEL_PRICING, get_model_pricing, estimate_cost, clean_model_name  # noqa: F401
from .cache import (
    CACHE_VERSION,
    get_claude_dir,
    get_projects_dir,
    get_cache_path,
    get_bookmarks_path,
    get_plan_path,
    load_bookmarks,
    save_bookmarks,
    load_metadata_cache,
    save_metadata_cache,
    load_plan,
    save_plan,
)
from .parser import (
    parse_conversation_metadata,
    parse_conversation_for_dashboard,
    parse_full_conversation,
    export_as_markdown,
)
from .store import ConversationStore
from .dashboard.aggregator import build_dashboard
from .dashboard.optimize import build_optimize
from .dashboard.compare import build_compare
from .dashboard.yield_tracker import build_yield
from .dashboard.plans import normalize_plan, list_presets
from .dashboard.export import export_dashboard


def decode_project_slug(slug: str) -> str:
    return slug



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
            # Legacy endpoint — kept for backward compatibility
            self._json(STORE.get_stats())

        elif path == "/api/dashboard":
            period = params.get("period", ["7d"])[0]
            from_str = params.get("from", [None])[0]
            to_str = params.get("to", [None])[0]
            plan = load_plan()
            payload = build_dashboard(
                STORE.conversations, STORE.dashboard_data,
                period=period, from_str=from_str, to_str=to_str, plan=plan,
            )
            self._json(payload)

        elif path == "/api/dashboard/optimize":
            period = params.get("period", ["30d"])[0]
            from_str = params.get("from", [None])[0]
            to_str = params.get("to", [None])[0]
            self._json(build_optimize(
                STORE.conversations, STORE.dashboard_data,
                period=period, from_str=from_str, to_str=to_str,
            ))

        elif path == "/api/dashboard/compare":
            period = params.get("period", ["30d"])[0]
            from_str = params.get("from", [None])[0]
            to_str = params.get("to", [None])[0]
            models = params.get("models", [""])[0]
            models_list = [m.strip() for m in models.split(",") if m.strip()] or None
            self._json(build_compare(
                STORE.conversations, STORE.dashboard_data,
                period=period, from_str=from_str, to_str=to_str,
                models_filter=models_list,
            ))

        elif path == "/api/dashboard/yield":
            period = params.get("period", ["7d"])[0]
            from_str = params.get("from", [None])[0]
            to_str = params.get("to", [None])[0]
            proj = params.get("project", [None])[0]
            self._json(build_yield(
                STORE.conversations, STORE.dashboard_data,
                period=period, from_str=from_str, to_str=to_str,
                project_filter=proj,
            ))

        elif path == "/api/dashboard/plan":
            plan = load_plan()
            self._json({"plan": plan, "presets": list_presets()})

        elif path == "/api/dashboard/export":
            period = params.get("period", ["30d"])[0]
            from_str = params.get("from", [None])[0]
            to_str = params.get("to", [None])[0]
            fmt = params.get("format", ["json"])[0]
            try:
                filename, body, ctype = export_dashboard(
                    STORE.conversations, STORE.dashboard_data,
                    fmt=fmt, period=period, from_str=from_str, to_str=to_str,
                )
            except ValueError as e:
                self._json({"error": str(e)}, 400)
                return
            self._download(body, filename, ctype)

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

        elif path == "/api/settings":
            update_info = check_for_update_sync() if check_for_update_sync is not None else {"update_available": False}
            self._json({
                "version": __version__,
                "package": "claude-chats-and-analytics-viewer",
                "projects_dir": str(get_projects_dir()),
                "conversations_count": len(STORE.conversations),
                "update": update_info,
            })

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

        elif path == "/api/dashboard/plan":
            preset = data.get("preset", "none")
            monthly = float(data.get("monthly_usd", 0.0) or 0.0)
            plan = normalize_plan(preset, monthly)
            save_plan(plan)
            self._json({"ok": True, "plan": plan})

        elif path == "/api/do-update":
            import subprocess, shutil as _shutil
            pkg = "claude-chats-and-analytics-viewer"
            try:
                if _shutil.which("pipx"):
                    result = subprocess.run(["pipx", "upgrade", pkg], capture_output=True, text=True, timeout=120)
                elif _shutil.which("uv"):
                    result = subprocess.run(["uv", "tool", "upgrade", pkg], capture_output=True, text=True, timeout=120)
                else:
                    result = subprocess.run(
                        [sys.executable, "-m", "pip", "install", "--upgrade", "--user", pkg],
                        capture_output=True, text=True, timeout=120,
                    )
                if result.returncode == 0:
                    self._json({"ok": True})
                else:
                    self._json({"ok": False, "error": (result.stderr or result.stdout or "Unknown error").strip()})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})

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
<title>Ledger — Claude Code accounts</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter+Tight:wght@300;400;500;600;700&family=Instrument+Serif:ital@0;1&family=JetBrains+Mono:wght@400;500;600&display=swap">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.1/marked.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  /* Neutrals — warm-tinted true-dark (not pure #000, not navy). */
  --bg:            oklch(0.09 0.006 40);       /* page */
  --bg-surface:    oklch(0.12 0.005 40);       /* sidebar */
  --bg-raised:     oklch(0.15 0.004 40);       /* cards */
  --bg-elevated:   oklch(0.19 0.004 40);       /* hover/active */
  --border:        oklch(0.24 0.004 40);
  --border-subtle: oklch(0.18 0.004 40);

  /* Text — warm off-white, never pure white. */
  --text:          oklch(0.97 0.006 75);
  --text-muted:    oklch(0.64 0.008 55);
  --text-faint:    oklch(0.42 0.006 45);

  /* Single accent: electric chartreuse. Used <10% of surface (Restrained). */
  --accent:        oklch(0.90 0.19 110);
  --accent-ink:    oklch(0.18 0.03 110);       /* text on accent bg */
  --accent-soft:   oklch(0.90 0.19 110 / 0.10);

  /* Functional */
  --positive:      oklch(0.78 0.16 145);
  --positive-soft: oklch(0.78 0.16 145 / 0.12);
  --negative:      oklch(0.70 0.18 28);
  --negative-soft: oklch(0.70 0.18 28 / 0.12);
  --warning:       oklch(0.82 0.14 75);
  --warning-soft:  oklch(0.82 0.14 75 / 0.12);
  --info:          oklch(0.78 0.10 225);
  --info-soft:     oklch(0.78 0.10 225 / 0.12);

  /* Message accents (kept compatible with existing viewer styling). */
  --user-accent:      oklch(0.75 0.15 235);
  --user-bg:          oklch(0.75 0.15 235 / 0.08);
  --user-border:      oklch(0.75 0.15 235 / 0.22);
  --asst-accent:      oklch(0.88 0.12 100);
  --asst-bg:          oklch(0.88 0.12 100 / 0.07);
  --asst-border:      oklch(0.88 0.12 100 / 0.20);
  --thinking-accent:  oklch(0.78 0.14 70);
  --thinking-bg:      oklch(0.78 0.14 70 / 0.06);
  --thinking-border:  oklch(0.78 0.14 70 / 0.22);
  --thinking-dim:     oklch(0.78 0.14 70 / 0.10);

  /* Backwards-compat aliases — preserve existing component CSS that used
     these names. New code should prefer the semantic tokens above. */
  --accent-bright: var(--accent);
  --accent-dim:    var(--accent-soft);
  --accent-glow:   oklch(0.90 0.19 110 / 0.05);
  --indigo:        var(--info);
  --indigo-dim:    var(--info-soft);
  --green:         var(--positive);
  --green-dim:     var(--positive-soft);
  --yellow:        var(--warning);
  --yellow-dim:    var(--warning-soft);
  --red:           var(--negative);
  --cyan:          var(--info);

  /* Geometry */
  --radius:        2px;
  --radius-sm:     2px;
  --radius-xs:     2px;
  --radius-pill:   999px;
  --ease-quint:    cubic-bezier(0.16, 1, 0.3, 1);

  /* Shadows — physical, warm-tinted. */
  --shadow:        0 1px 0 oklch(1 0 0 / 0.02) inset, 0 20px 40px -24px oklch(0 0 0 / 0.6);
  --shadow-sm:     0 1px 0 oklch(1 0 0 / 0.02) inset;

  /* Type — single clean sans for everything; scale + weight carry hierarchy. */
  --font-ui:      'Inter Tight', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  --font-display: 'Inter Tight', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  --font-mono:    'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace;
}

html { background: var(--bg); }

body {
  font-family: var(--font-ui);
  background: var(--bg);
  color: var(--text);
  height: 100vh;
  overflow: hidden;
  font-size: 13.5px;
  line-height: 1.5;
  letter-spacing: -0.005em;
  font-feature-settings: 'cv11', 'ss01', 'ss03', 'zero';
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  text-rendering: optimizeLegibility;
}

/* Selection: accent + ink. */
::selection { background: var(--accent); color: var(--accent-ink); }

/* Numeric text should always be tabular for dashboards. */
.tabular, [data-numeric] { font-variant-numeric: tabular-nums; }

/* ── Layout ── */
.app { display: flex; height: 100vh; }

/* ── Sidebar ── */
.sidebar {
  width: 320px;
  min-width: 320px;
  background: var(--bg-surface);
  border-right: 1px solid var(--border-subtle);
  display: flex;
  flex-direction: column;
  height: 100vh;
  position: relative;
}

.sidebar-header {
  padding: 22px 20px 12px;
  border-bottom: 1px solid var(--border-subtle);
  flex-shrink: 0;
}

.brand {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 24px;
  flex-wrap: wrap;
}

/* Wordmark: bold sans, with a chartreuse period. */
.brand-icon {
  font-family: var(--font-display);
  font-weight: 700;
  font-size: 22px;
  line-height: 1;
  color: var(--text);
  letter-spacing: -0.03em;
  flex-shrink: 0;
}
.brand-icon::after {
  content: ".";
  color: var(--accent);
  margin-left: 1px;
}

.brand-text {
  font-size: 10px;
  font-weight: 500;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--text-faint);
  font-family: var(--font-mono);
  align-self: flex-end;
  padding-bottom: 3px;
}
.brand-sub { display: none; }

.brand-actions { margin-left: auto; display: flex; gap: 2px; }

.icon-btn {
  width: 28px;
  height: 28px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--text-muted);
  border-radius: var(--radius-sm);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  transition: color 140ms var(--ease-quint), background 140ms var(--ease-quint), border-color 140ms var(--ease-quint);
  flex-shrink: 0;
}
.icon-btn:hover { color: var(--text); background: var(--bg-raised); border-color: var(--border-subtle); }
.icon-btn.active { color: var(--accent-ink); background: var(--accent); border-color: var(--accent); }

/* Tabs: text links with an underline for the active tab — no pill bar. */
.tabs {
  display: flex;
  gap: 20px;
  margin-bottom: 16px;
  border-bottom: 1px solid var(--border-subtle);
  margin-left: -20px;
  margin-right: -20px;
  padding: 0 20px;
  overflow-x: auto;
  scrollbar-width: none;
}
.tabs::-webkit-scrollbar { display: none; }

.tab-btn {
  padding: 10px 0 12px;
  border: none;
  background: transparent;
  color: var(--text-faint);
  cursor: pointer;
  font-size: 12.5px;
  font-weight: 500;
  font-family: var(--font-ui);
  letter-spacing: -0.005em;
  white-space: nowrap;
  position: relative;
  transition: color 140ms var(--ease-quint);
}
.tab-btn::after {
  content: "";
  position: absolute;
  left: 0; right: 0; bottom: -1px;
  height: 1px;
  background: var(--text);
  transform: scaleX(0);
  transform-origin: left;
  transition: transform 240ms var(--ease-quint);
}
.tab-btn.active { color: var(--text); }
.tab-btn.active::after { transform: scaleX(1); }
.tab-btn:hover:not(.active) { color: var(--text-muted); }

.search-wrap { position: relative; margin-bottom: 10px; }

.search-icon {
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  color: var(--text-faint);
  font-size: 13px;
  pointer-events: none;
}

/* Search is now a bare input with a bottom rule — borderless, CRED-style. */
.search-box {
  width: 100%;
  padding: 10px 48px 10px 18px;
  background: transparent;
  border: none;
  border-bottom: 1px solid var(--border-subtle);
  color: var(--text);
  font-family: var(--font-ui);
  font-size: 13px;
  outline: none;
  transition: border-color 200ms var(--ease-quint);
}
.search-box:focus { border-bottom-color: var(--accent); }
.search-box::placeholder { color: var(--text-faint); letter-spacing: -0.01em; }

.search-mode-toggle {
  position: absolute;
  right: 0;
  top: 50%;
  transform: translateY(-50%);
  padding: 4px 8px;
  font-size: 10px;
  font-weight: 600;
  border: 1px solid var(--border-subtle);
  background: transparent;
  color: var(--text-faint);
  border-radius: var(--radius-pill);
  cursor: pointer;
  transition: color 140ms var(--ease-quint), border-color 140ms var(--ease-quint), background 140ms var(--ease-quint);
  letter-spacing: 0.08em;
  font-family: var(--font-mono);
}
.search-mode-toggle:hover { color: var(--text); border-color: var(--text-muted); }
.search-mode-toggle.active { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }

.filter-row { display: flex; gap: 10px; margin-top: 2px; }

.filter-select {
  flex: 1;
  min-width: 0;
  padding: 6px 2px;
  background: transparent;
  border: none;
  border-bottom: 1px solid var(--border-subtle);
  color: var(--text-muted);
  font-family: var(--font-ui);
  font-size: 12px;
  outline: none;
  cursor: pointer;
  transition: color 140ms var(--ease-quint), border-color 140ms var(--ease-quint);
  appearance: none;
  background-image: linear-gradient(45deg, transparent 50%, var(--text-faint) 50%),
                    linear-gradient(135deg, var(--text-faint) 50%, transparent 50%);
  background-position: calc(100% - 10px) 50%, calc(100% - 6px) 50%;
  background-size: 4px 4px;
  background-repeat: no-repeat;
  padding-right: 18px;
}
.filter-select:hover { color: var(--text); }
.filter-select:focus { border-bottom-color: var(--accent); color: var(--text); }

/* ── Conversation list ── */
.conv-list { flex: 1; overflow-y: auto; padding: 4px 0; }

.conv-item {
  padding: 14px 20px;
  cursor: pointer;
  border: none;
  border-bottom: 1px solid var(--border-subtle);
  margin: 0;
  transition: background 140ms var(--ease-quint);
  position: relative;
}
.conv-item:hover { background: var(--bg-raised); }
.conv-item.active {
  background: var(--bg-raised);
}
.conv-item.active::before {
  content: "";
  position: absolute;
  left: 0; top: 0; bottom: 0;
  width: 2px;
  background: var(--accent);
}

.conv-item-top { display: flex; align-items: baseline; gap: 8px; margin-bottom: 4px; }

.conv-project {
  font-size: 10px;
  font-weight: 600;
  color: var(--text-faint);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  flex: 1;
  min-width: 0;
  font-family: var(--font-mono);
}

.bookmark-btn {
  background: none;
  border: none;
  cursor: pointer;
  color: var(--text-faint);
  font-size: 12px;
  padding: 0;
  line-height: 1;
  flex-shrink: 0;
  transition: color 140ms var(--ease-quint);
}
.bookmark-btn:hover { color: var(--accent); }
.bookmark-btn.bookmarked { color: var(--accent); }

.conv-title {
  font-size: 13.5px;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 4px;
  color: var(--text);
  line-height: 1.35;
  letter-spacing: -0.005em;
}

.conv-preview {
  font-size: 12px;
  color: var(--text-muted);
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  line-height: 1.5;
  margin-bottom: 8px;
  word-break: break-word;
}

.conv-meta {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 11px;
  color: var(--text-faint);
  flex-wrap: wrap;
  font-variant-numeric: tabular-nums;
}

.meta-sep { color: var(--text-faint); opacity: 0.5; }

/* Cost: tabular mono, subtle green. No pill. */
.cost-badge {
  color: var(--positive);
  font-size: 11px;
  font-weight: 500;
  background: transparent;
  padding: 0;
  border: none;
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.01em;
}

/* Model: mono tag. No pill gradient. */
.model-badge {
  color: var(--text-muted);
  font-size: 10px;
  background: transparent;
  padding: 0;
  border: none;
  font-family: var(--font-mono);
  letter-spacing: 0.02em;
  max-width: 140px;
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
   white-space: pre-wrap; word-break: break-word;
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

/* ── Dashboard panel — editorial layout ── */
.dashboard-panel {
  padding: 32px 48px 80px;
  overflow-y: auto;
  height: 100%;
  max-width: 1400px;
}

/* Masthead: oversized serif headline + period label, like a statement header. */
.dashboard-header {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: end;
  gap: 24px;
  padding-bottom: 20px;
  margin-bottom: 28px;
  border-bottom: 1px solid var(--border-subtle);
}
.dashboard-header h2 {
  font-family: var(--font-display);
  font-weight: 700;
  font-size: 40px;
  letter-spacing: -0.035em;
  color: var(--text);
  line-height: 0.95;
}
.dashboard-header .dash-range {
  font-size: 10px;
  color: var(--text-faint);
  font-variant-numeric: tabular-nums;
  font-family: var(--font-mono);
  letter-spacing: 0.14em;
  text-transform: uppercase;
  text-align: right;
  white-space: nowrap;
}

/* Sub-tabs: text links underlined, no pill container. */
.dash-subtabs {
  display: flex;
  gap: 28px;
  border-bottom: 1px solid var(--border-subtle);
  margin-bottom: 28px;
  overflow-x: auto;
  scrollbar-width: none;
}
.dash-subtabs::-webkit-scrollbar { display: none; }
.dash-subtab {
  padding: 0 0 14px;
  font-size: 12.5px;
  font-weight: 500;
  color: var(--text-faint);
  background: transparent;
  border: none;
  cursor: pointer;
  transition: color 140ms var(--ease-quint);
  position: relative;
  font-family: var(--font-ui);
  letter-spacing: -0.005em;
  white-space: nowrap;
}
.dash-subtab::after {
  content: "";
  position: absolute;
  left: 0; right: 0; bottom: -1px;
  height: 1px;
  background: var(--text);
  transform: scaleX(0);
  transform-origin: left;
  transition: transform 260ms var(--ease-quint);
}
.dash-subtab.active { color: var(--text); }
.dash-subtab.active::after { transform: scaleX(1); }
.dash-subtab:hover:not(.active) { color: var(--text-muted); }

/* Controls row: period as text links, export as text buttons. */
.dash-controls {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 18px;
  margin-bottom: 40px;
}
.period-pills {
  display: flex;
  gap: 2px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-pill);
  padding: 2px;
}
.period-pill {
  padding: 5px 14px;
  font-size: 11.5px;
  font-weight: 500;
  color: var(--text-faint);
  background: transparent;
  border: none;
  border-radius: var(--radius-pill);
  cursor: pointer;
  transition: color 160ms var(--ease-quint), background 160ms var(--ease-quint);
  font-family: var(--font-ui);
  letter-spacing: -0.005em;
}
.period-pill.active { background: var(--text); color: var(--bg); }
.period-pill:hover:not(.active) { color: var(--text); }

.dash-actions { margin-left: auto; display: flex; gap: 4px; align-items: center; }
.dash-btn {
  background: transparent;
  border: 1px solid transparent;
  color: var(--text-muted);
  border-radius: var(--radius-pill);
  padding: 6px 12px;
  font-size: 11.5px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  transition: color 140ms var(--ease-quint), border-color 140ms var(--ease-quint);
  font-family: var(--font-ui);
  letter-spacing: -0.005em;
}
.dash-btn:hover { color: var(--text); border-color: var(--border-subtle); }
.dash-btn.primary { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); font-weight: 600; }
.dash-btn.primary:hover { filter: brightness(1.05); }

/* ── Statement: one hero number + supporting metrics ── */
.statement {
  display: grid;
  grid-template-columns: 1.4fr 1fr;
  gap: 64px;
  padding-bottom: 48px;
  margin-bottom: 40px;
  border-bottom: 1px solid var(--border-subtle);
  align-items: start;
}
.statement-hero .stat-label,
.stat-pair .stat-label {
  font-size: 10px;
  font-family: var(--font-mono);
  color: var(--text-faint);
  letter-spacing: 0.14em;
  text-transform: uppercase;
  margin-bottom: 14px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.statement-hero .stat-label::after,
.stat-pair .stat-label::after {
  content: "";
  flex: 1;
  height: 1px;
  background: var(--border-subtle);
}
.statement-hero .stat-value {
  font-family: var(--font-display);
  font-weight: 600;
  font-size: 88px;
  line-height: 0.95;
  letter-spacing: -0.045em;
  color: var(--text);
  font-variant-numeric: tabular-nums;
  margin-bottom: 14px;
}
.statement-hero .stat-value .currency {
  font-size: 36px;
  vertical-align: 28px;
  color: var(--text-faint);
  font-weight: 400;
  margin-right: 6px;
  letter-spacing: -0.02em;
}
.statement-hero .stat-sub {
  font-size: 13px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.005em;
  max-width: 30ch;
  line-height: 1.45;
}
.statement-hero .stat-sub .sep { color: var(--text-faint); margin: 0 10px; }

.statement-secondary {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px 32px;
  padding-top: 32px;
}
.stat-pair .stat-value {
  font-family: var(--font-display);
  font-weight: 600;
  font-size: 32px;
  line-height: 1;
  letter-spacing: -0.025em;
  color: var(--text);
  font-variant-numeric: tabular-nums;
  margin-bottom: 6px;
}
.stat-pair .stat-value.positive { color: var(--positive); }
.stat-pair .stat-value.accent { color: var(--accent); }
.stat-pair .stat-sub {
  font-size: 11px;
  color: var(--text-faint);
  font-family: var(--font-mono);
  letter-spacing: 0.04em;
}

/* ── Section rhythm: numbered editorial headers ── */
.dash-section { margin-bottom: 56px; position: relative; }
.dash-section-head {
  display: flex;
  align-items: baseline;
  gap: 16px;
  margin-bottom: 24px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--border-subtle);
}
.dash-section-head .idx {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-faint);
  letter-spacing: 0.12em;
}
.dash-section-head h3 {
  font-family: var(--font-display);
  font-weight: 600;
  font-size: 24px;
  color: var(--text);
  letter-spacing: -0.02em;
  line-height: 1;
  text-transform: none;
}
.dash-section-head .dash-section-note {
  font-size: 11px;
  color: var(--text-faint);
  margin-left: auto;
  font-family: var(--font-mono);
  letter-spacing: 0.04em;
}

.dash-grid-2 { display: grid; grid-template-columns: 1.3fr 1fr; gap: 48px; }
.dash-grid-3 { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 32px; }

/* Cards are rare — prefer hairline-bordered regions. .dash-card is a plain panel. */
.dash-card {
  background: transparent;
  border: none;
  padding: 0;
}
.dash-card h4 {
  font-size: 10px;
  font-weight: 500;
  color: var(--text-faint);
  text-transform: uppercase;
  letter-spacing: 0.12em;
  margin-bottom: 18px;
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  font-family: var(--font-mono);
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border-subtle);
}
.dash-card h4 .hint {
  font-weight: 400;
  text-transform: none;
  letter-spacing: 0;
  color: var(--text-faint);
  font-size: 10.5px;
  font-family: var(--font-ui);
}

/* ── Tables: editorial; hairline rules, tabular numerals. ── */
.dash-table { width: 100%; border-collapse: collapse; font-size: 13px; font-variant-numeric: tabular-nums; }
.dash-table th, .dash-table td {
  text-align: left;
  padding: 11px 12px 11px 0;
  border-bottom: 1px solid var(--border-subtle);
}
.dash-table th:last-child, .dash-table td:last-child { padding-right: 0; }
.dash-table th {
  font-size: 10px;
  font-weight: 500;
  color: var(--text-faint);
  text-transform: uppercase;
  letter-spacing: 0.12em;
  border-bottom: 1px solid var(--border);
  padding-top: 0;
  padding-bottom: 12px;
  font-family: var(--font-mono);
}
.dash-table tr:last-child td { border-bottom: none; }
.dash-table tr { transition: background 120ms var(--ease-quint); }
.dash-table tbody tr:hover { background: var(--bg-raised); }
.dash-table td.num, .dash-table th.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
  font-family: var(--font-mono);
  letter-spacing: -0.01em;
}
.dash-table td.truncate { max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dash-table td.mono { font-family: var(--font-mono); font-size: 11.5px; color: var(--text-muted); }

/* ── Bar chart rows — thin, monochrome, accent only for highlighted ── */
.bar-row { display: flex; align-items: center; gap: 16px; padding: 8px 0; }
.bar-label {
  min-width: 140px;
  font-size: 12px;
  color: var(--text);
  text-align: right;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  letter-spacing: -0.005em;
}
.bar-track {
  flex: 1;
  height: 4px;
  background: var(--bg-raised);
  border-radius: 2px;
  overflow: hidden;
  border: none;
  position: relative;
}
.bar-fill {
  height: 100%;
  background: var(--text);
  border-radius: 2px;
  transition: width 420ms var(--ease-quint);
  font-size: 0;
  display: block;
  padding: 0;
}
.bar-value {
  font-size: 11.5px;
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.01em;
  min-width: 80px;
  text-align: right;
}
.bar-fill.green { background: var(--positive); }
.bar-fill.cyan  { background: var(--info); }
.bar-fill.amber { background: var(--warning); }
.bar-fill.accent { background: var(--accent); }

/* ── Daily cost chart ── */
.daily-chart-wrap {
  position: relative;
  height: 200px;
  border-top: 1px solid var(--border-subtle);
  border-bottom: 1px solid var(--border-subtle);
  padding: 16px 0;
}
.daily-chart-wrap canvas { width: 100%; height: 100%; display: block; }
.daily-chart-legend {
  display: flex;
  gap: 10px;
  justify-content: space-between;
  font-size: 10px;
  color: var(--text-faint);
  margin-top: 10px;
  font-variant-numeric: tabular-nums;
  font-family: var(--font-mono);
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

/* ── GitHub-style activity heatmap ── */
.activity-heatmap {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.activity-months {
  display: flex;
  justify-content: space-between;
  font-size: 10px;
  color: var(--text-faint);
  font-family: var(--font-mono);
  letter-spacing: 0.1em;
  text-transform: uppercase;
  padding-left: 20px;
}
.activity-body {
  display: flex;
  gap: 8px;
}
.activity-days {
  display: flex;
  flex-direction: column;
  justify-content: space-around;
  font-size: 9px;
  color: var(--text-faint);
  font-family: var(--font-mono);
  letter-spacing: 0.08em;
  padding-top: 2px;
  min-width: 12px;
  gap: 2px;
}
.activity-grid {
  display: flex;
  gap: 3px;
  flex: 1;
  overflow-x: auto;
  scrollbar-width: none;
}
.activity-grid::-webkit-scrollbar { display: none; }
.activity-week { display: flex; flex-direction: column; gap: 3px; flex-shrink: 0; }
.activity-cell {
  width: 11px;
  height: 11px;
  border-radius: 2px;
  background: var(--bg-raised);
  transition: transform 140ms var(--ease-quint);
  cursor: default;
}
.activity-cell:hover { transform: scale(1.35); }
.activity-cell.l1 { background: oklch(0.90 0.19 110 / 0.22); }
.activity-cell.l2 { background: oklch(0.90 0.19 110 / 0.45); }
.activity-cell.l3 { background: oklch(0.90 0.19 110 / 0.72); }
.activity-cell.l4 { background: var(--accent); }
.activity-legend {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 4px;
  font-size: 10px;
  color: var(--text-faint);
  font-family: var(--font-mono);
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.activity-legend .activity-cell { width: 10px; height: 10px; }

/* ── Health grade — typographic, not a box ── */
.health-tile {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 28px;
  padding: 28px 0;
  margin-bottom: 40px;
  border-top: 1px solid var(--border-subtle);
  border-bottom: 1px solid var(--border-subtle);
}
.health-grade {
  font-family: var(--font-display);
  font-weight: 600;
  font-size: 96px;
  line-height: 0.9;
  letter-spacing: -0.04em;
  color: var(--text);
  border: none;
  background: transparent;
  padding: 0;
  width: auto; height: auto;
}
.health-grade.A { color: var(--positive); }
.health-grade.B { color: var(--info); }
.health-grade.C { color: var(--warning); }
.health-grade.D { color: var(--negative); }
.health-grade.F { color: var(--negative); }

.health-text { display: flex; flex-direction: column; gap: 6px; }
.health-text .big {
  font-size: 20px;
  font-weight: 400;
  letter-spacing: -0.015em;
  color: var(--text);
  font-family: var(--font-display);
  font-weight: 600;
  line-height: 1.1;
}
.health-text .small {
  font-size: 11px;
  color: var(--text-faint);
  font-variant-numeric: tabular-nums;
  font-family: var(--font-mono);
  letter-spacing: 0.04em;
}
.health-score {
  font-family: var(--font-display);
  font-weight: 600;
  font-size: 40px;
  color: var(--text-muted);
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
}
.health-score .max { font-size: 24px; color: var(--text-faint); vertical-align: 12px; }

/* ── Optimize findings — editorial cards with leading numerals. ── */
.finding {
  display: grid;
  grid-template-columns: 48px 1fr auto;
  gap: 20px;
  padding: 24px 0;
  border: none;
  border-bottom: 1px solid var(--border-subtle);
  border-radius: 0;
  margin: 0;
  background: transparent;
  align-items: start;
}
.finding-number {
  font-family: var(--font-mono);
  font-weight: 500;
  font-size: 14px;
  line-height: 1;
  color: var(--text-faint);
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
}
.finding-main { display: flex; flex-direction: column; gap: 10px; }
.finding-head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.finding-title {
  font-size: 18px;
  font-weight: 400;
  color: var(--text);
  font-family: var(--font-display);
  font-weight: 600;
  letter-spacing: -0.015em;
  line-height: 1.2;
}
.finding-impact {
  font-size: 9.5px;
  font-weight: 500;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  padding: 2px 0;
  font-family: var(--font-mono);
  background: transparent;
  border: none;
  border-radius: 0;
  line-height: 1;
}
.finding-impact::before {
  content: "·";
  margin-right: 8px;
  color: currentColor;
}
.finding-impact.high { color: var(--negative); }
.finding-impact.medium { color: var(--warning); }
.finding-impact.low { color: var(--info); }
.finding-tokens {
  font-size: 10px;
  color: var(--text-faint);
  font-family: var(--font-mono);
  letter-spacing: 0.04em;
  text-align: right;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  padding-top: 4px;
}
.finding-body {
  font-size: 13.5px;
  line-height: 1.55;
  color: var(--text-muted);
  max-width: 68ch;
  letter-spacing: -0.005em;
}
.finding-fix {
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  padding: 12px 14px;
  font-size: 11.5px;
  color: var(--text);
  margin-top: 4px;
}
.finding-fix-label {
  font-size: 10px;
  color: var(--text-faint);
  text-transform: uppercase;
  letter-spacing: 0.12em;
  margin-bottom: 8px;
  display: flex;
  justify-content: space-between;
  font-family: var(--font-mono);
}
.finding-fix pre {
  font-family: var(--font-mono);
  font-size: 11.5px;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--accent);
  line-height: 1.6;
  letter-spacing: 0;
}
.copy-btn {
  background: transparent;
  border: none;
  color: var(--text-faint);
  font-size: 10px;
  cursor: pointer;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  font-family: var(--font-mono);
  transition: color 140ms var(--ease-quint);
}
.copy-btn:hover { color: var(--accent); }

/* ── Compare grid ── */
.compare-grid { display: grid; gap: 0; }
.compare-grid > div {
  padding: 12px 16px 12px 0;
  border-bottom: 1px solid var(--border-subtle);
  font-size: 13.5px;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.005em;
}
.compare-grid > div:first-child { padding-left: 0; }
.compare-grid .compare-header {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--text-faint);
  font-weight: 500;
  border-bottom: 1px solid var(--border);
  font-family: var(--font-mono);
  padding-top: 0;
  padding-bottom: 12px;
}
.compare-grid .compare-section {
  grid-column: 1/-1;
  font-size: 10px;
  color: var(--text-faint);
  text-transform: uppercase;
  letter-spacing: 0.14em;
  padding: 28px 0 12px;
  font-weight: 500;
  font-family: var(--font-mono);
  border-bottom: none;
}
.compare-grid .compare-metric { color: var(--text-muted); }
.compare-grid .compare-value {
  color: var(--text);
  font-family: var(--font-mono);
  letter-spacing: -0.01em;
}

/* ── Yield: stacked bar (single hue, opacity-graded). ── */
.yield-pie { display: flex; gap: 16px; align-items: center; }
.yield-bar {
  flex: 1; display: flex; height: 6px; border-radius: 3px; overflow: hidden;
  border: none; background: var(--bg-raised);
}
.yield-seg-productive { background: var(--positive); }
.yield-seg-reverted   { background: var(--negative); }
.yield-seg-abandoned  { background: var(--text-faint); }
.yield-seg-no-git     { background: var(--bg-elevated); }
.yield-legend {
  display: flex; gap: 18px; flex-wrap: wrap; font-size: 11px;
  color: var(--text-muted); margin-top: 14px;
  font-family: var(--font-mono); letter-spacing: 0.04em;
  font-variant-numeric: tabular-nums;
}
.yield-legend .dot {
  width: 8px; height: 8px; border-radius: 2px; display: inline-block; margin-right: 6px;
  vertical-align: 0.5px;
}

/* ── Plan progress ── */
.plan-bar-wrap { display: flex; align-items: center; gap: 16px; margin-top: 10px; }
.plan-bar {
  flex: 1; height: 2px;
  background: var(--border-subtle);
  overflow: hidden; border-radius: 1px;
}
.plan-bar-fill {
  height: 100%;
  background: var(--accent);
  transition: width 520ms var(--ease-quint);
}
.plan-pct {
  font-size: 12px; color: var(--text);
  font-variant-numeric: tabular-nums;
  min-width: 52px; text-align: right;
  font-family: var(--font-mono); letter-spacing: -0.01em;
}

/* ── Category tag — muted; single hue via text color only. ── */
.cat-tag {
  display: inline-block; padding: 0;
  font-size: 11.5px; font-weight: 400; letter-spacing: -0.005em;
  background: transparent; color: var(--text-muted);
  font-family: var(--font-ui);
  position: relative; padding-left: 10px;
}
.cat-tag::before {
  content: ""; position: absolute; left: 0; top: 50%;
  width: 4px; height: 4px; border-radius: 50%; transform: translateY(-50%);
  background: currentColor;
}
.cat-tag.coding        { color: oklch(0.82 0.14 280); }
.cat-tag.feature       { color: oklch(0.82 0.14 220); }
.cat-tag.debugging     { color: oklch(0.74 0.17 25);  }
.cat-tag.refactoring   { color: oklch(0.80 0.14 65);  }
.cat-tag.testing       { color: var(--positive);      }
.cat-tag.exploration   { color: oklch(0.78 0.12 205); }
.cat-tag.planning      { color: oklch(0.80 0.14 310); }
.cat-tag.delegation    { color: oklch(0.78 0.15 340); }
.cat-tag.git           { color: oklch(0.78 0.15 50);  }
.cat-tag\/build\/deploy { color: var(--warning);      }
.cat-tag.brainstorming { color: oklch(0.85 0.12 200); }
.cat-tag.conversation  { color: var(--text-faint);    }
.cat-tag.general       { color: var(--text-faint);    }
.cat-tag.productive    { color: var(--positive);      }
.cat-tag.reverted      { color: var(--negative);      }
.cat-tag.abandoned     { color: var(--text-faint);    }
.cat-tag.no-git        { color: var(--text-faint);    }

.one-shot-bar {
  display: inline-flex; align-items: center; gap: 8px;
  font-variant-numeric: tabular-nums;
  font-family: var(--font-mono); font-size: 11px;
  color: var(--text-muted);
  letter-spacing: -0.01em;
}
.one-shot-bar .mini-track {
  width: 50px; height: 2px;
  background: var(--border-subtle);
  overflow: hidden; border: none; border-radius: 1px;
}
.one-shot-bar .mini-fill { height: 100%; background: var(--positive); }
.one-shot-bar.warn .mini-fill { background: var(--warning); }
.one-shot-bar.bad .mini-fill { background: var(--negative); }

.dash-empty {
  padding: 24px 0; text-align: left;
  color: var(--text-faint); font-size: 12.5px;
  font-family: var(--font-ui);
}

/* ── Settings panel ── */
.settings-panel {
  padding: 40px 48px 80px; overflow-y: auto; height: 100%;
  max-width: 720px;
}
.settings-panel h2 {
  font-family: var(--font-display); font-weight: 700;
  font-size: 36px; letter-spacing: -0.03em;
  margin-bottom: 32px; padding-bottom: 20px;
  border-bottom: 1px solid var(--border-subtle);
  color: var(--text);
}
.settings-section {
  background: transparent; border: none;
  padding: 0; margin-bottom: 36px;
  border-bottom: 1px solid var(--border-subtle);
  padding-bottom: 28px;
}
.settings-section:last-of-type { border-bottom: none; }
.settings-section-title {
  font-size: 10px; font-weight: 500;
  text-transform: uppercase; letter-spacing: 0.14em;
  color: var(--text-faint); margin-bottom: 18px;
  font-family: var(--font-mono);
}
.settings-row {
  display: flex; align-items: baseline; gap: 16px;
  padding: 10px 0;
  border-bottom: 1px solid var(--border-subtle);
}
.settings-row:last-of-type { border-bottom: none; }
.settings-label {
  font-size: 11.5px; color: var(--text-muted);
  min-width: 110px; flex-shrink: 0;
  letter-spacing: -0.005em;
}
.settings-value {
  font-size: 13.5px; color: var(--text);
  font-variant-numeric: tabular-nums; letter-spacing: -0.005em;
}
.settings-actions { display: flex; gap: 8px; margin-top: 18px; flex-wrap: wrap; }
.settings-btn {
  background: transparent;
  border: 1px solid var(--border-subtle);
  color: var(--text);
  border-radius: var(--radius-pill);
  padding: 7px 16px; font-size: 12px; cursor: pointer;
  font-family: var(--font-ui); letter-spacing: -0.005em;
  transition: color 140ms var(--ease-quint), border-color 140ms var(--ease-quint);
}
.settings-btn:hover { background: var(--bg-surface); border-color: var(--accent); }
.settings-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.settings-btn-primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.settings-btn-primary:hover { background: var(--accent-bright); border-color: var(--accent-bright); }

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
.update-banner .update-now-btn { background: var(--accent); color: #fff; border: none; border-radius: 4px; padding: 3px 10px; font-size: 12px; cursor: pointer; flex-shrink: 0; }
.update-banner .update-now-btn:hover { background: var(--accent-bright); }
.update-banner .update-now-btn:disabled { opacity: 0.6; cursor: not-allowed; }

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
  .dashboard-panel { padding: 16px; }
  .dash-kpis { grid-template-columns: repeat(2, 1fr); gap: 10px; }
  .kpi-card .kpi-value { font-size: 18px; }
  .bar-label { min-width: 100px; }
  .filter-row { flex-direction: column; }
  .dash-grid-2 { grid-template-columns: 1fr; }
  .compare-grid { grid-template-columns: 1fr; }
  .compare-grid > div { border-bottom: 1px solid var(--border-subtle); }
}

@media (max-width: 480px) {
  .sidebar-header { padding: 10px; }
  .conv-list { padding: 4px; }
  .conv-item { padding: 9px 10px; }
  .dash-kpis { grid-template-columns: 1fr 1fr; gap: 8px; }
  .dashboard-header { flex-direction: column; align-items: flex-start; }
  .dash-controls { flex-direction: column; align-items: stretch; }
  .dash-actions { margin-left: 0; }
}
</style>
</head>
<body>

<div class="update-banner" id="updateBanner">
  <span id="updateBannerText">&#10022; Update available!</span>
  <div style="display:flex;align-items:center;gap:8px;flex-shrink:0;">
    <button class="update-now-btn" id="updateNowBtn" onclick="doUpdate()">Update Now</button>
    <button class="dismiss-btn" onclick="dismissUpdate()" title="Dismiss">&times;</button>
  </div>
</div>

<div class="app" id="app">
  <!-- Sidebar -->
  <div class="sidebar">
    <div class="sidebar-header">
      <div class="brand">
        <div class="brand-icon">Ledger</div>
        <div class="brand-text">Claude&nbsp;Code accounts</div>
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
        <button class="tab-btn" data-tab="dashboard" onclick="switchTab('dashboard')">Dashboard</button>
        <button class="tab-btn" data-tab="settings" onclick="switchTab('settings')">&#9881; Settings</button>
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

    <div class="dashboard-panel" id="dashboardPanel" style="display:none">
      <div class="loading"><div class="spinner"></div> Loading dashboard…</div>
    </div>

    <div class="settings-panel" id="settingsPanel" style="display:none">
      <h2>Settings</h2>

      <div class="settings-section">
        <div class="settings-section-title">About</div>
        <div class="settings-row">
          <span class="settings-label">Version</span>
          <span class="settings-value" id="settingsVersion">—</span>
        </div>
        <div class="settings-row">
          <span class="settings-label">Package</span>
          <span class="settings-value" id="settingsPackage" style="font-size:11px;word-break:break-all;">—</span>
        </div>
      </div>

      <div class="settings-section">
        <div class="settings-section-title">Updates</div>
        <div class="settings-row">
          <span class="settings-label">Installed</span>
          <span class="settings-value" id="settingsCurrent">—</span>
        </div>
        <div class="settings-row">
          <span class="settings-label">Latest</span>
          <span class="settings-value" id="settingsLatest">—</span>
        </div>
        <div class="settings-row">
          <span class="settings-label">Status</span>
          <span class="settings-value" id="settingsUpdateStatus">Checking…</span>
        </div>
        <div class="settings-actions">
          <button class="settings-btn" id="settingsCheckBtn" onclick="settingsCheckUpdate()">Check for Updates</button>
          <button class="settings-btn settings-btn-primary" id="settingsUpdateBtn" style="display:none" onclick="settingsDoUpdate()">Update Now</button>
        </div>
        <div id="settingsUpdateMsg" style="margin-top:8px;font-size:12px;color:var(--text-muted);display:none;"></div>
      </div>

      <div class="settings-section">
        <div class="settings-section-title">Storage</div>
        <div class="settings-row">
          <span class="settings-label">Projects dir</span>
          <span class="settings-value" id="settingsProjectsDir" style="font-size:11px;word-break:break-all;">—</span>
        </div>
        <div class="settings-row">
          <span class="settings-label">Conversations</span>
          <span class="settings-value" id="settingsConvCount">—</span>
        </div>
      </div>
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
    if (d.update_available) {
      const verStr = (d.current_version && d.latest_version)
        ? ` v${d.current_version} → v${d.latest_version}` : '';
      document.getElementById('updateBannerText').innerHTML =
        `&#10022; Update available!${verStr}`;
      document.getElementById('updateBanner').classList.add('show');
    }
  } catch {}
}
function dismissUpdate() {
  document.getElementById('updateBanner').classList.remove('show');
  sessionStorage.setItem('update-dismissed', '1');
}
async function doUpdate() {
  const btn = document.getElementById('updateNowBtn');
  const txt = document.getElementById('updateBannerText');
  btn.disabled = true;
  btn.textContent = 'Updating…';
  txt.textContent = '⟳ Installing update, please wait…';
  try {
    const r = await fetch('/api/do-update', { method: 'POST' });
    const d = await r.json();
    if (d.ok) {
      txt.innerHTML = '✓ Updated! Restart to apply: <code>ccv</code>';
      btn.style.display = 'none';
    } else {
      txt.innerHTML = `✗ Update failed. Run <code>pip install --upgrade claude-chats-and-analytics-viewer</code> manually.`;
      btn.textContent = 'Retry';
      btn.disabled = false;
    }
  } catch {
    txt.innerHTML = `✗ Update failed. Run <code>pip install --upgrade claude-chats-and-analytics-viewer</code> manually.`;
    btn.textContent = 'Retry';
    btn.disabled = false;
  }
}

// ── Settings ──
async function loadSettings() {
  try {
    const r = await fetch('/api/settings');
    const d = await r.json();
    document.getElementById('settingsVersion').textContent = 'v' + d.version;
    document.getElementById('settingsPackage').textContent = d.package;
    document.getElementById('settingsProjectsDir').textContent = d.projects_dir;
    document.getElementById('settingsConvCount').textContent = d.conversations_count;
    const u = d.update || {};
    document.getElementById('settingsCurrent').textContent = u.current_version ? 'v' + u.current_version : '—';
    document.getElementById('settingsLatest').textContent = u.latest_version ? 'v' + u.latest_version : '—';
    if (u.update_available) {
      document.getElementById('settingsUpdateStatus').innerHTML = '<span style="color:var(--accent)">Update available</span>';
      document.getElementById('settingsUpdateBtn').style.display = 'inline-block';
    } else {
      document.getElementById('settingsUpdateStatus').innerHTML = '<span style="color:#4ade80">Up to date</span>';
      document.getElementById('settingsUpdateBtn').style.display = 'none';
    }
  } catch {
    document.getElementById('settingsUpdateStatus').textContent = 'Could not check';
  }
}
async function settingsCheckUpdate() {
  const btn = document.getElementById('settingsCheckBtn');
  const statusEl = document.getElementById('settingsUpdateStatus');
  btn.disabled = true;
  btn.textContent = 'Checking…';
  statusEl.textContent = 'Checking…';
  try {
    const r = await fetch('/api/update-check');
    const d = await r.json();
    document.getElementById('settingsCurrent').textContent = d.current_version ? 'v' + d.current_version : '—';
    document.getElementById('settingsLatest').textContent = d.latest_version ? 'v' + d.latest_version : '—';
    if (d.update_available) {
      statusEl.innerHTML = '<span style="color:var(--accent)">Update available</span>';
      document.getElementById('settingsUpdateBtn').style.display = 'inline-block';
    } else {
      statusEl.innerHTML = '<span style="color:#4ade80">Up to date</span>';
      document.getElementById('settingsUpdateBtn').style.display = 'none';
    }
  } catch {
    statusEl.textContent = 'Check failed';
  }
  btn.disabled = false;
  btn.textContent = 'Check for Updates';
}
async function settingsDoUpdate() {
  const btn = document.getElementById('settingsUpdateBtn');
  const msg = document.getElementById('settingsUpdateMsg');
  btn.disabled = true;
  btn.textContent = 'Updating…';
  msg.style.display = 'block';
  msg.textContent = 'Installing update, please wait…';
  try {
    const r = await fetch('/api/do-update', { method: 'POST' });
    const d = await r.json();
    if (d.ok) {
      msg.innerHTML = '✓ Updated! Restart to apply: <code style="background:var(--bg-elevated);padding:1px 5px;border-radius:3px;">ccv</code>';
      btn.style.display = 'none';
      document.getElementById('settingsUpdateStatus').innerHTML = '<span style="color:#4ade80">Updated — restart required</span>';
    } else {
      msg.textContent = '✗ Update failed. Run: pip install --upgrade claude-chats-and-analytics-viewer';
      btn.disabled = false;
      btn.textContent = 'Retry';
    }
  } catch {
    msg.textContent = '✗ Update failed. Run: pip install --upgrade claude-chats-and-analytics-viewer';
    btn.disabled = false;
    btn.textContent = 'Retry';
  }
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
  const dash = document.getElementById('dashboardPanel');
  const settings = document.getElementById('settingsPanel');
  const searchWrap = document.getElementById('searchWrap');
  const filterRow = document.querySelector('.filter-row');

  if (tab === 'dashboard') {
    header.style.display = 'none';
    msgs.style.display = 'none';
    dash.style.display = 'block';
    settings.style.display = 'none';
    loadDashboard();
  } else if (tab === 'settings') {
    header.style.display = 'none';
    msgs.style.display = 'none';
    dash.style.display = 'none';
    settings.style.display = 'block';
    loadSettings();
  } else {
    header.style.display = 'block';
    msgs.style.display = 'block';
    dash.style.display = 'none';
    settings.style.display = 'none';
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
  if (currentTab === 'dashboard') return;
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

// ── Dashboard ──
const DASH_STATE = { period: '7d', subtab: 'overview', lastData: null, lastOptimize: null, lastCompare: null, lastYield: null, findings: [] };
const PERIODS = [
  {key: 'today', label: 'Today'},
  {key: '7d',    label: '7 Days'},
  {key: '30d',   label: '30 Days'},
  {key: 'month', label: 'Month'},
  {key: 'all',   label: 'All Time'},
];

async function loadDashboard() {
  const panel = document.getElementById('dashboardPanel');
  panel.innerHTML = '<div class="loading"><div class="spinner"></div> Loading dashboard…</div>';
  await renderDashboardShell();
  await renderSubtab();
}

async function renderDashboardShell() {
  const panel = document.getElementById('dashboardPanel');
  panel.innerHTML = `
    <div class="dashboard-header">
      <h2>Dashboard</h2>
      <div class="dash-range" id="dashRangeLabel">—</div>
    </div>
    <div class="dash-subtabs">
      ${['overview','optimize','compare','yield','plan'].map(s =>
        `<button class="dash-subtab${DASH_STATE.subtab===s?' active':''}" onclick="setDashSubtab('${s}')">${s.charAt(0).toUpperCase()+s.slice(1)}</button>`
      ).join('')}
    </div>
    <div class="dash-controls">
      <div class="period-pills" id="periodPills">
        ${PERIODS.map(p => `<button class="period-pill${DASH_STATE.period===p.key?' active':''}" data-period="${p.key}" onclick="setDashPeriod('${p.key}')">${p.label}</button>`).join('')}
      </div>
      <div class="dash-actions">
        <button class="dash-btn" onclick="downloadDashboard('csv')" title="Download CSV">&#8623; CSV</button>
        <button class="dash-btn" onclick="downloadDashboard('json')" title="Download JSON">&#8623; JSON</button>
        <button class="dash-btn" onclick="loadDashboard()" title="Refresh">&#8635; Refresh</button>
      </div>
    </div>
    <div id="dashBody"></div>`;
}

async function setDashPeriod(p) {
  DASH_STATE.period = p;
  document.querySelectorAll('.period-pill').forEach(b => b.classList.toggle('active', b.dataset.period === p));
  await renderSubtab();
}

async function setDashSubtab(s) {
  DASH_STATE.subtab = s;
  document.querySelectorAll('.dash-subtab').forEach(b => b.classList.toggle('active', b.textContent.toLowerCase() === s));
  await renderSubtab();
}

async function renderSubtab() {
  const body = document.getElementById('dashBody');
  body.innerHTML = '<div class="loading"><div class="spinner"></div> Computing…</div>';
  const s = DASH_STATE.subtab;
  if (s === 'overview') await renderOverview();
  else if (s === 'optimize') await renderOptimize();
  else if (s === 'compare') await renderCompare();
  else if (s === 'yield') await renderYield();
  else if (s === 'plan') await renderPlan();
}

function rangeLabel(d) {
  if (!d || !d.range) return '';
  if (!d.range.from) return 'All time';
  const from = d.range.from.slice(0,10);
  const to = d.range.to ? d.range.to.slice(0,10) : '—';
  return `${from} → ${to}`;
}

async function renderOverview() {
  const body = document.getElementById('dashBody');
  const [dr, hr] = await Promise.all([
    fetch(`/api/dashboard?period=${DASH_STATE.period}`).then(r => r.json()),
    fetch('/api/dashboard?period=all').then(r => r.json()),
  ]);
  const d = dr;
  const allData = hr;
  DASH_STATE.lastData = d;
  document.getElementById('dashRangeLabel').textContent = rangeLabel(d);

  const ov = d.overview;
  const plan = d.plan;

  const planHtml = plan ? `
    <div class="dash-section" style="margin-bottom:40px;">
      <div class="dash-section-head">
        <span class="idx">00</span>
        <h3>Plan</h3>
        <span class="dash-section-note">${escapeHtml(plan.preset)} · $${plan.monthly_usd}/mo</span>
      </div>
      <div class="plan-bar-wrap">
        <div class="plan-bar"><div class="plan-bar-fill" style="width:${Math.min(plan.percent_used,100)}%"></div></div>
        <div class="plan-pct">${plan.percent_used}%</div>
      </div>
      <div style="font-size:11.5px;color:var(--text-muted);margin-top:10px;letter-spacing:-0.005em;">
        ${formatCost(plan.month_cost)} API-equivalent spend this month against a $${plan.monthly_usd} plan price.
      </div>
    </div>` : '';

  // Hero statement — cost as the single dominant number
  const costFormatted = formatCost(ov.cost);
  const [costCurr, ...costRest] = costFormatted.split('');
  const statement = `
    <div class="statement">
      <div class="statement-hero">
        <div class="stat-label">spend &middot; ${DASH_STATE.period}</div>
        <div class="stat-value" data-numeric><span class="currency">${escapeHtml(costCurr || '$')}</span>${escapeHtml(costRest.join(''))}</div>
        <div class="stat-sub">
          <span>${ov.calls.toLocaleString()} calls</span>
          <span class="sep">/</span>
          <span>${ov.sessions} sessions</span>
          <span class="sep">/</span>
          <span>avg ${formatCost(ov.avg_cost_per_session)}</span>
        </div>
      </div>
      <div class="statement-secondary">
        <div class="stat-pair">
          <div class="stat-label">today</div>
          <div class="stat-value positive" data-numeric>${formatCost(ov.today_cost)}</div>
          <div class="stat-sub">single day</div>
        </div>
        <div class="stat-pair">
          <div class="stat-label">month</div>
          <div class="stat-value positive" data-numeric>${formatCost(ov.month_cost)}</div>
          <div class="stat-sub">calendar month</div>
        </div>
        <div class="stat-pair">
          <div class="stat-label">cache</div>
          <div class="stat-value accent" data-numeric>${ov.cache_hit_rate_pct != null ? ov.cache_hit_rate_pct + '%' : '—'}</div>
          <div class="stat-sub">${formatTokens(ov.cache_read_tokens)} cached</div>
        </div>
        <div class="stat-pair">
          <div class="stat-label">tokens</div>
          <div class="stat-value" data-numeric>${formatTokens(ov.total_tokens)}</div>
          <div class="stat-sub">${formatTokens(ov.output_tokens)} output</div>
        </div>
      </div>
    </div>`;

  const heatmap = renderActivityHeatmap(allData.daily || []);
  const dailyChart = renderDailyChart(d.daily);
  const activities = renderActivities(d.activities);
  const models = renderModels(d.models);
  const projects = renderProjects(d.projects);
  const core = renderCoreTools(d.core_tools);
  const shell = renderShell(d.shell);
  const mcp = renderMcp(d.mcp);
  const top = renderTopSessions(d.top_sessions);

  body.innerHTML = planHtml + statement + `
    <div class="dash-section">
      <div class="dash-section-head">
        <span class="idx">01</span>
        <h3>Activity</h3>
        <span class="dash-section-note">last 52 weeks</span>
      </div>
      ${heatmap}
    </div>
    <div class="dash-section">
      <div class="dash-section-head">
        <span class="idx">02</span>
        <h3>Daily cost</h3>
        <span class="dash-section-note">${d.daily.length} days</span>
      </div>
      ${dailyChart}
    </div>
    <div class="dash-section">
      <div class="dash-section-head">
        <span class="idx">03</span>
        <h3>Activities &amp; models</h3>
        <span class="dash-section-note">one-shot = edits without retry</span>
      </div>
      <div class="dash-grid-2">
        <div class="dash-card"><h4>By category</h4>${activities}</div>
        <div class="dash-card"><h4>By model</h4>${models}</div>
      </div>
    </div>
    <div class="dash-section">
      <div class="dash-section-head">
        <span class="idx">04</span>
        <h3>Projects &amp; tools</h3>
      </div>
      <div class="dash-grid-2">
        <div class="dash-card"><h4>Top projects</h4>${projects}</div>
        <div class="dash-card"><h4>Core tools</h4>${core}</div>
      </div>
    </div>
    <div class="dash-section">
      <div class="dash-section-head">
        <span class="idx">05</span>
        <h3>Shell &amp; MCP</h3>
      </div>
      <div class="dash-grid-2">
        <div class="dash-card"><h4>Shell commands</h4>${shell}</div>
        <div class="dash-card"><h4>MCP servers</h4>${mcp}</div>
      </div>
    </div>
    <div class="dash-section">
      <div class="dash-section-head">
        <span class="idx">06</span>
        <h3>Expensive sessions</h3>
        <span class="dash-section-note">top 5 this period</span>
      </div>
      ${top}
    </div>`;
  setTimeout(drawDailyChart, 20);
}

// Build GitHub-style heatmap. Uses the `all-time` daily payload.
function renderActivityHeatmap(daily) {
  const today = new Date();
  // Align to Sunday-start week
  const endDay = new Date(today);
  endDay.setHours(0, 0, 0, 0);
  const totalDays = 52 * 7;
  const startDay = new Date(endDay);
  startDay.setDate(endDay.getDate() - totalDays + 1);
  // Back up to previous Sunday
  startDay.setDate(startDay.getDate() - startDay.getDay());

  const costByDay = {};
  let maxCost = 0;
  daily.forEach(r => {
    costByDay[r.date] = r.cost;
    if (r.cost > maxCost) maxCost = r.cost;
  });

  const cells = [];
  const cur = new Date(startDay);
  while (cur <= endDay) {
    const key = cur.toISOString().slice(0, 10);
    const c = costByDay[key] || 0;
    cells.push({ date: key, cost: c });
    cur.setDate(cur.getDate() + 1);
  }

  const weeks = [];
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));

  const levelFor = (c) => {
    if (c === 0 || maxCost === 0) return 0;
    const r = c / maxCost;
    if (r < 0.15) return 1;
    if (r < 0.4) return 2;
    if (r < 0.7) return 3;
    return 4;
  };

  // Month labels — name appears over the first week that falls in that month
  const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const monthLabels = weeks.map((week, i) => {
    const first = new Date(week[0].date);
    if (i === 0) return MONTHS[first.getMonth()];
    const prev = new Date(weeks[i - 1][0].date);
    return first.getMonth() !== prev.getMonth() ? MONTHS[first.getMonth()] : '';
  });

  const weeksHtml = weeks.map((week, wi) => {
    return `<div class="activity-week">` +
      week.map(d => {
        const lvl = levelFor(d.cost);
        const title = d.cost > 0 ? `${d.date} · ${formatCost(d.cost)}` : `${d.date} · no activity`;
        return `<div class="activity-cell${lvl ? ' l'+lvl : ''}" title="${title}"></div>`;
      }).join('') + `</div>`;
  }).join('');

  // Render month label row with gaps — one label per week slot
  const monthRow = monthLabels.map((m, i) => {
    return `<span style="flex:0 0 14px;text-align:left;">${m}</span>`;
  }).join('');

  return `
    <div class="activity-heatmap">
      <div class="activity-months" style="display:flex;gap:0;padding-left:20px;">${monthRow}</div>
      <div class="activity-body">
        <div class="activity-days"><span>Mon</span><span>Wed</span><span>Fri</span></div>
        <div class="activity-grid">${weeksHtml}</div>
      </div>
      <div class="activity-legend">
        Less
        <div class="activity-cell"></div>
        <div class="activity-cell l1"></div>
        <div class="activity-cell l2"></div>
        <div class="activity-cell l3"></div>
        <div class="activity-cell l4"></div>
        More
      </div>
    </div>`;
}

function renderActivities(items) {
  if (!items || !items.length) return '<div class="dash-empty">No data in this period</div>';
  const maxCalls = Math.max(...items.map(a => a.calls), 1);
  const rows = items.filter(a => a.calls > 0).map(a => {
    const pct = (a.calls / maxCalls * 100).toFixed(0);
    let oneShotHtml = '—';
    if (a.one_shot_rate != null) {
      const cls = a.one_shot_rate >= 75 ? '' : (a.one_shot_rate >= 50 ? 'warn' : 'bad');
      oneShotHtml = `<span class="one-shot-bar ${cls}"><span class="mini-track"><span class="mini-fill" style="width:${a.one_shot_rate}%"></span></span>${a.one_shot_rate}%</span>`;
    }
    return `<tr>
      <td><span class="cat-tag ${a.category.replace('/','\\/')}">${escapeHtml(a.category)}</span></td>
      <td class="num">${formatCost(a.cost)}</td>
      <td class="num">${a.calls}</td>
      <td class="num">${a.edit_turns || '—'}</td>
      <td class="num">${oneShotHtml}</td>
    </tr>`;
  }).join('');
  return `<table class="dash-table">
    <thead><tr><th>Activity</th><th class="num">Cost</th><th class="num">Calls</th><th class="num">Edits</th><th class="num">1-shot</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

function renderModels(items) {
  if (!items || !items.length) return '<div class="dash-empty">No data</div>';
  const max = Math.max(...items.map(m => m.cost), 0.0001);
  return items.map((m, i) => {
    const pct = (m.cost / max * 100).toFixed(0);
    const cls = i === 0 ? ' accent' : '';
    return `<div class="bar-row">
      <div class="bar-label" title="${escapeHtml(m.model)}">${escapeHtml(m.label)}</div>
      <div class="bar-track"><div class="bar-fill${cls}" style="width:${pct}%"></div></div>
      <div class="bar-value">${formatCost(m.cost)} · ${m.calls}</div>
    </div>`;
  }).join('');
}

function renderProjects(items) {
  if (!items || !items.length) return '<div class="dash-empty">No data</div>';
  const rows = items.slice(0, 10).map(p => `<tr>
    <td class="truncate" title="${escapeHtml(p.project)}">${escapeHtml(shortenPath(p.project))}</td>
    <td class="num">${formatCost(p.cost)}</td>
    <td class="num">${p.sessions}</td>
    <td class="num">${formatCost(p.avg_cost_per_session)}</td>
  </tr>`).join('');
  return `<table class="dash-table">
    <thead><tr><th>Project</th><th class="num">Cost</th><th class="num">Sessions</th><th class="num">Avg/sess</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

function renderCoreTools(items) {
  if (!items || !items.length) return '<div class="dash-empty">No data</div>';
  const max = Math.max(...items.map(t => t.count), 1);
  return items.map(t => {
    const pct = (t.count / max * 100).toFixed(0);
    return `<div class="bar-row">
      <div class="bar-label">${escapeHtml(t.tool)}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${pct}%"></div></div>
      <div class="bar-value">${t.count}</div>
    </div>`;
  }).join('');
}

function renderShell(items) {
  if (!items || !items.length) return '<div class="dash-empty">No shell usage</div>';
  const max = Math.max(...items.map(t => t.count), 1);
  return items.slice(0, 15).map(t => {
    const pct = (t.count / max * 100).toFixed(0);
    return `<div class="bar-row">
      <div class="bar-label" title="${escapeHtml(t.command)}" style="font-family:var(--font-mono);font-size:11.5px;color:var(--text-muted);">${escapeHtml(t.command)}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${pct}%"></div></div>
      <div class="bar-value">${t.count}</div>
    </div>`;
  }).join('');
}

function renderMcp(items) {
  if (!items || !items.length) return '<div class="dash-empty">No MCP activity. Configure MCP servers in ~/.claude.json to see breakdown.</div>';
  const max = Math.max(...items.map(t => t.count), 1);
  return items.map(t => {
    const pct = (t.count / max * 100).toFixed(0);
    return `<div class="bar-row">
      <div class="bar-label">${escapeHtml(t.server)}</div>
      <div class="bar-track"><div class="bar-fill green" style="width:${pct}%"></div></div>
      <div class="bar-value">${t.count}</div>
    </div>`;
  }).join('');
}

function renderTopSessions(items) {
  if (!items || !items.length) return '<div class="dash-empty">No sessions in this period</div>';
  const rows = items.map(s => `<tr onclick="jumpToSession('${s.session_id}')" style="cursor:pointer;">
    <td class="truncate" title="${escapeHtml(s.title)}">${escapeHtml(s.title || s.session_id.slice(0,10))}</td>
    <td class="truncate" title="${escapeHtml(s.project_path)}">${escapeHtml(shortenPath(s.project_path))}</td>
    <td class="num">${formatCost(s.cost)}</td>
    <td class="num">${s.calls}</td>
    <td class="mono">${(s.first_timestamp||'').slice(0,10)}</td>
  </tr>`).join('');
  return `<table class="dash-table">
    <thead><tr><th>Session</th><th>Project</th><th class="num">Cost</th><th class="num">Calls</th><th>Date</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

function jumpToSession(id) {
  switchTab('conversations');
  setTimeout(() => loadConversation(id), 50);
}

// ── Daily cost chart (canvas) ──
function renderDailyChart(daily) {
  return `<div class="daily-chart-wrap"><canvas id="dailyCanvas"></canvas></div>
    <div class="daily-chart-legend">
      <span>${daily.length ? daily[0].date : ''}</span>
      <span>Peak ${formatCost(Math.max(0, ...daily.map(d=>d.cost)))}</span>
      <span>${daily.length ? daily[daily.length-1].date : ''}</span>
    </div>`;
}

function drawDailyChart() {
  const canvas = document.getElementById('dailyCanvas');
  if (!canvas || !DASH_STATE.lastData) return;
  const data = DASH_STATE.lastData.daily || [];
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, rect.width, rect.height);

  if (!data.length) return;
  const max = Math.max(...data.map(d => d.cost), 0.0001);
  const w = rect.width;
  const h = rect.height;
  const padL = 36, padR = 10, padT = 8, padB = 22;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;

  // Read theme tokens from CSS so the chart always matches theme.
  const cs = getComputedStyle(document.documentElement);
  const accent = cs.getPropertyValue('--accent').trim() || '#E3FF70';
  const faint = cs.getPropertyValue('--text-faint').trim() || '#555';
  const subtle = cs.getPropertyValue('--border-subtle').trim() || '#333';

  // Y-grid (3 hairlines)
  ctx.strokeStyle = subtle;
  ctx.lineWidth = 1;
  ctx.fillStyle = faint;
  ctx.font = '10px "JetBrains Mono", monospace';
  ctx.textAlign = 'right';
  for (let i = 0; i <= 3; i++) {
    const y = padT + plotH * (i / 3);
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR, y); ctx.stroke();
    const val = max * (1 - i / 3);
    ctx.fillText(val > 0 ? '$' + val.toFixed(val >= 10 ? 0 : 1) : '$0', padL - 6, y + 3);
  }

  // Bars — solid accent with slight translucency toward base
  const barW = Math.max(plotW / data.length * 0.68, 2);
  const step = plotW / data.length;
  const gap = step * 0.32;
  data.forEach((d, i) => {
    const x = padL + i * step + gap / 2;
    const bh = (d.cost / max) * plotH;
    const y = padT + plotH - bh;
    ctx.fillStyle = d.cost > 0 ? accent : subtle;
    ctx.globalAlpha = d.cost > 0 ? 1 : 0.5;
    ctx.fillRect(x, y, barW, Math.max(bh, d.cost > 0 ? 1.5 : 1));
    ctx.globalAlpha = 1;
  });

  // X-axis labels (first / mid / last)
  ctx.textAlign = 'center';
  ctx.fillStyle = faint;
  const labelIdxs = [0, Math.floor(data.length / 2), data.length - 1];
  labelIdxs.forEach(i => {
    if (i < 0 || i >= data.length) return;
    const x = padL + i * step + step / 2;
    ctx.fillText(data[i].date.slice(5), x, h - 6);
  });
}

window.addEventListener('resize', () => {
  if (DASH_STATE.subtab === 'overview' && currentTab === 'dashboard') drawDailyChart();
});

// ── Optimize ──
async function renderOptimize() {
  const body = document.getElementById('dashBody');
  const r = await fetch(`/api/dashboard/optimize?period=${DASH_STATE.period}`);
  const d = await r.json();
  DASH_STATE.lastOptimize = d;
  DASH_STATE.findings = d.findings;
  document.getElementById('dashRangeLabel').textContent = rangeLabel(d);

  const health = `<div class="health-tile">
    <div class="health-grade ${d.grade}">${d.grade}</div>
    <div class="health-text">
      <div class="big">Setup health</div>
      <div class="small">${d.findings.length} finding${d.findings.length === 1 ? '' : 's'} · ${d.turns_scanned.toLocaleString()} turns · ${DASH_STATE.period}</div>
    </div>
    <div class="health-score" data-numeric>${d.score}<span class="max">/100</span></div>
  </div>`;

  const findings = d.findings.length ? `
    <div class="dash-section-head" style="border:none;margin-bottom:8px;">
      <span class="idx">01</span><h3>Waste patterns</h3>
      <span class="dash-section-note">${d.findings.length} finding${d.findings.length === 1 ? '' : 's'}</span>
    </div>
    ` + d.findings.map((f, i) => `
    <div class="finding">
      <div class="finding-number">${String(i + 1).padStart(2, '0')}</div>
      <div class="finding-main">
        <div class="finding-head">
          <span class="finding-title">${escapeHtml(f.title)}</span>
          <span class="finding-impact ${f.impact}">${f.impact}</span>
        </div>
        <div class="finding-body">${escapeHtml(f.explanation)}</div>
        <div class="finding-fix">
          <div class="finding-fix-label">
            <span>${escapeHtml(f.fix.label)}</span>
            <button class="copy-btn" onclick="copyFindingFix(${i})">Copy</button>
          </div>
          <pre id="findingFix${i}">${escapeHtml(f.fix.text)}</pre>
        </div>
      </div>
      <div class="finding-tokens">~${formatTokens(f.tokens_saved)}<br>tokens saved</div>
    </div>`).join('') : `
    <div class="dash-empty" style="padding:80px 20px;text-align:center;">
      <div style="font-family:var(--font-display);font-style:italic;font-size:48px;color:var(--positive);margin-bottom:12px;">clean.</div>
      <div style="color:var(--text-muted);font-size:13px;">No waste patterns detected in this window.</div>
    </div>`;

  body.innerHTML = health + findings;
}

function copyFindingFix(i) {
  const f = DASH_STATE.findings[i];
  if (!f) return;
  navigator.clipboard.writeText(f.fix.text).then(() => {
    const btn = event.target;
    const t = btn.textContent;
    btn.textContent = 'Copied!';
    setTimeout(() => { btn.textContent = t; }, 1200);
  });
}

// ── Compare ──
async function renderCompare() {
  const body = document.getElementById('dashBody');
  const r = await fetch(`/api/dashboard/compare?period=${DASH_STATE.period}`);
  const d = await r.json();
  DASH_STATE.lastCompare = d;
  document.getElementById('dashRangeLabel').textContent = rangeLabel(d);

  if (!d.models.length) {
    body.innerHTML = '<div class="dash-empty">No model usage in this period.</div>';
    return;
  }

  const hdr = '<div class="compare-header">Metric</div>' + d.models.map(m =>
    `<div class="compare-header">${escapeHtml(m.label)}<div style="font-weight:400;color:var(--text-muted);font-size:10.5px;margin-top:2px;">${m.calls} calls · ${formatCost(m.cost)}</div></div>`
  ).join('');

  function row(label, getter, fmt) {
    const cells = d.models.map(m => {
      const v = getter(m);
      return `<div class="compare-value">${v == null ? '—' : fmt(v)}</div>`;
    }).join('');
    return `<div class="compare-metric">${label}</div>${cells}`;
  }

  const section = (title) => `<div class="compare-section">${title}</div>`;
  const pct = v => v + '%';
  const num = v => v.toFixed(2);
  const tok = v => formatTokens(v);
  const cost = v => formatCost(v);

  const gridCols = `220px ${d.models.map(()=>'minmax(140px, 1fr)').join(' ')}`;
  body.innerHTML = `<div class="compare-grid" style="grid-template-columns: ${gridCols};">
    ${hdr}
    ${section('Performance')}
    ${row('One-shot rate',       m => m.performance?.one_shot_rate_pct, pct)}
    ${row('Retry rate / edit',   m => m.performance?.retry_rate, num)}
    ${row('Self-correction %',   m => m.performance?.self_correction_pct, pct)}
    ${section('Efficiency')}
    ${row('Cost per call',       m => m.efficiency?.cost_per_call, cost)}
    ${row('Cost per edit',       m => m.efficiency?.cost_per_edit, cost)}
    ${row('Output tokens / call', m => m.efficiency?.output_tokens_per_call, tok)}
    ${row('Cache hit rate',      m => m.efficiency?.cache_hit_rate_pct, pct)}
    ${section('Behavior')}
    ${row('Delegation rate',     m => m.behavior?.delegation_rate_pct, pct)}
    ${row('Planning rate',       m => m.behavior?.planning_rate_pct, pct)}
    ${row('Avg tools / turn',    m => m.behavior?.avg_tools_per_turn, num)}
  </div>`;
}

// ── Yield ──
async function renderYield() {
  const body = document.getElementById('dashBody');
  const r = await fetch(`/api/dashboard/yield?period=${DASH_STATE.period}`);
  const d = await r.json();
  DASH_STATE.lastYield = d;
  document.getElementById('dashRangeLabel').textContent = rangeLabel(d);

  const b = d.breakdown;
  const total = d.total_sessions || 1;
  const segs = ['productive','reverted','abandoned','no-git'].map(k => {
    const w = (b[k]?.sessions || 0) / total * 100;
    return `<div class="yield-seg-${k}" style="flex:0 0 ${w}%;"></div>`;
  }).join('');

  const legend = [
    ['productive','Productive','#22c55e'],
    ['reverted','Reverted','#ef4444'],
    ['abandoned','Abandoned','#64748b'],
    ['no-git','No git','transparent'],
  ].map(([k, label, dot]) => `<span><span class="dot" style="background:${dot};${dot==='transparent'?'border:1px solid var(--border);':''}"></span>${label} ${b[k]?.sessions||0} · ${formatCost(b[k]?.cost||0)}</span>`).join('');

  const rows = (d.sessions || []).slice(0, 25).map(s => `<tr>
    <td class="truncate" title="${escapeHtml(s.title)}">${escapeHtml(s.title || s.session_id.slice(0,10))}</td>
    <td class="truncate" title="${escapeHtml(s.project_path)}">${escapeHtml(shortenPath(s.project_path))}</td>
    <td class="num">${formatCost(s.cost)}</td>
    <td><span class="cat-tag ${s.status}">${escapeHtml(s.status)}</span></td>
    <td class="num">${s.commits.length}</td>
  </tr>`).join('');

  body.innerHTML = `
    <div class="dash-card" style="margin-bottom:16px;">
      <h4>Session outcomes · ${d.total_sessions} total</h4>
      <div class="yield-pie"><div class="yield-bar">${segs}</div></div>
      <div class="yield-legend">${legend}</div>
    </div>
    <div class="dash-card">
      <h4>Sessions by outcome</h4>
      ${rows ? `<table class="dash-table">
        <thead><tr><th>Session</th><th>Project</th><th class="num">Cost</th><th>Status</th><th class="num">Commits</th></tr></thead>
        <tbody>${rows}</tbody></table>` : '<div class="dash-empty">No sessions to classify</div>'}
    </div>
    <div style="font-size:11px;color:var(--text-muted);margin-top:10px;">
      Yield correlates session timestamps with <code>git log</code> in each session's <code>cwd</code>.
      "Abandoned" = no commit inside the session window. "Reverted" = a later <code>git revert</code> touched the commit.
    </div>`;
}

// ── Plan ──
async function renderPlan() {
  const body = document.getElementById('dashBody');
  const r = await fetch('/api/dashboard/plan');
  const d = await r.json();
  const current = d.plan;
  document.getElementById('dashRangeLabel').textContent = '';

  const presets = d.presets.map(p => `
    <label style="display:flex;gap:10px;align-items:center;padding:10px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);cursor:pointer;background:${current.preset===p.key?'var(--accent-dim)':'var(--bg-surface)'};margin-bottom:8px;">
      <input type="radio" name="plan" value="${p.key}" ${current.preset===p.key?'checked':''}>
      <div style="flex:1;">
        <div style="font-size:13px;font-weight:600;">${escapeHtml(p.label)}</div>
        <div style="font-size:11px;color:var(--text-muted);">$${p.monthly_usd}/month</div>
      </div>
    </label>`).join('');

  const customChecked = current.preset === 'custom';
  body.innerHTML = `
    <div class="dash-card" style="max-width:500px;">
      <h4>Subscription plan</h4>
      <div style="font-size:12px;color:var(--text-muted);margin-bottom:14px;">
        Track API-equivalent spend against your plan price. Presets use publicly stated prices; they do not model token allowances.
      </div>
      ${presets}
      <label style="display:flex;gap:10px;align-items:center;padding:10px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);cursor:pointer;background:${customChecked?'var(--accent-dim)':'var(--bg-surface)'};margin-bottom:8px;">
        <input type="radio" name="plan" value="custom" ${customChecked?'checked':''}>
        <div style="flex:1;">
          <div style="font-size:13px;font-weight:600;">Custom</div>
          <div style="font-size:11px;color:var(--text-muted);margin-bottom:6px;">Set your own monthly budget</div>
          <input type="number" id="planCustomUsd" value="${customChecked?current.monthly_usd:100}" min="0" step="5" style="width:120px;background:var(--bg-raised);border:1px solid var(--border);color:var(--text);border-radius:4px;padding:4px 8px;font-size:12px;"> USD/month
        </div>
      </label>
      <label style="display:flex;gap:10px;align-items:center;padding:10px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);cursor:pointer;background:${current.preset==='none'?'var(--accent-dim)':'var(--bg-surface)'};margin-bottom:16px;">
        <input type="radio" name="plan" value="none" ${current.preset==='none'?'checked':''}>
        <div style="flex:1;">
          <div style="font-size:13px;font-weight:600;">No plan</div>
          <div style="font-size:11px;color:var(--text-muted);">Hide the plan progress bar</div>
        </div>
      </label>
      <button class="dash-btn primary" onclick="savePlan()">Save plan</button>
    </div>`;
}

async function savePlan() {
  const selected = document.querySelector('input[name="plan"]:checked');
  if (!selected) return;
  const preset = selected.value;
  const monthly = preset === 'custom' ? parseFloat(document.getElementById('planCustomUsd').value) : 0;
  await fetch('/api/dashboard/plan', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({preset, monthly_usd: monthly}),
  });
  await renderPlan();
}

function downloadDashboard(fmt) {
  window.open(`/api/dashboard/export?format=${fmt}&period=${DASH_STATE.period}`, '_blank');
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
Description=Ledger (Claude Code accounts)
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
    parser = argparse.ArgumentParser(description="Ledger — Claude Code accounts (web)")
    parser.add_argument("--port", type=int, default=5005)
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--install", action="store_true", help="Install macOS LaunchAgent")
    parser.add_argument("--uninstall", action="store_true", help="Remove macOS LaunchAgent")
    parser.add_argument("--install-systemd", action="store_true", help="Install Linux systemd user service")
    parser.add_argument("--update", action="store_true", help="Update to latest version from PyPI")
    args = parser.parse_args()

    if args.update:
        import subprocess, shutil
        pkg = "claude-chats-and-analytics-viewer"
        print(f"Updating {pkg}...")
        if shutil.which("pipx"):
            result = subprocess.run(["pipx", "upgrade", pkg])
        elif shutil.which("uv"):
            result = subprocess.run(["uv", "tool", "upgrade", pkg])
        else:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", "--user", pkg]
            )
        if result.returncode == 0:
            print("Updated! Restart with: ccv")
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
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        print(f"\n  Port {args.port} is already in use — switching to port {port}")
        print(f"  Tip: use 'ccv --port {port}' next time to use this port directly.")
        server = HTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"

    print(f"\n  Ledger · Claude Code accounts  v{__version__}")
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
