"""Session store — scans ~/.claude/projects/, parses JSONL, holds in memory.

Responsible for:
* Discovery (walking the projects directory)
* Cache hit / miss logic (mtime + size keyed)
* Both the metadata blob and per-turn classifier data (cache v3)
* Content-search across all sessions
* Bookmark persistence
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional

from .cache import (
    CACHE_VERSION,
    get_projects_dir,
    load_bookmarks,
    load_metadata_cache,
    save_metadata_cache,
)
from .parser import (
    parse_conversation_for_dashboard,
    parse_conversation_metadata,
)


class ConversationStore:
    def __init__(self):
        self.conversations: List[dict] = []
        self.by_id: Dict[str, dict] = {}
        self.projects: List[str] = []
        self._file_map: Dict[str, Path] = {}
        # Per-session dashboard data: {session_id: {"turns": [...]}}
        self.dashboard_data: Dict[str, dict] = {}
        self.bookmarks: Dict[str, bool] = {}
        self.last_scanned: float = 0

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

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
        dashboard_data: Dict[str, dict] = {}

        for jsonl_file in all_files:
            file_key = str(jsonl_file)
            try:
                stat = jsonl_file.stat()
                mtime = stat.st_mtime
                size = stat.st_size
            except OSError:
                continue

            cached = cache.get(file_key)
            meta: Optional[dict]
            dash: Optional[dict]
            if (
                cached
                and cached.get("mtime") == mtime
                and cached.get("size") == size
                and "dashboard" in cached
            ):
                meta = cached["metadata"]
                dash = cached["dashboard"]
                cache_hits += 1
            else:
                meta = parse_conversation_metadata(jsonl_file)
                dash = parse_conversation_for_dashboard(jsonl_file) if meta else None
                parsed += 1

            if meta:
                new_cache[file_key] = {
                    "mtime": mtime,
                    "size": size,
                    "metadata": meta,
                    "dashboard": dash or {"turns": []},
                }
                conversations.append(meta)
                file_map[meta["id"]] = jsonl_file
                dashboard_data[meta["id"]] = dash or {"turns": []}

        conversations.sort(key=lambda c: c.get("last_timestamp") or "", reverse=True)

        self.conversations = conversations
        self.by_id = {c["id"]: c for c in conversations}
        self._file_map = file_map
        self.dashboard_data = dashboard_data
        self.projects = sorted(set(c["project"] for c in conversations))
        self.bookmarks = load_bookmarks()
        self.last_scanned = time.time()

        save_metadata_cache(new_cache)
        total = len(conversations)
        print(
            f"[INFO] {total} conversations ({cache_hits} cached, {parsed} parsed, cache v{CACHE_VERSION}) "
            f"from {len(self.projects)} projects"
        )

    # ------------------------------------------------------------------
    # Stats (legacy endpoint — kept for backward compat)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Content search
    # ------------------------------------------------------------------

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
