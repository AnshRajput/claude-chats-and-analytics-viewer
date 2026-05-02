"""CSV + JSON export for dashboard data."""

from __future__ import annotations

import csv
import io
import json
from typing import Dict, List, Optional

from .aggregator import build_dashboard


def export_dashboard(conversations: List[dict],
                     dashboard_data: Dict[str, dict],
                     fmt: str = "json",
                     period: Optional[str] = "30d",
                     from_str: Optional[str] = None,
                     to_str: Optional[str] = None) -> tuple:
    """Return (filename, bytes, content_type) for the requested format."""
    payload = build_dashboard(conversations, dashboard_data, period, from_str, to_str)

    if fmt == "json":
        body = json.dumps(payload, indent=2).encode("utf-8")
        return (f"dashboard-{payload['period']}.json", body, "application/json")

    # CSV — flat tables, one per tab in a zip
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)

        writer.writerow(["section", "key", "value"])
        ov = payload["overview"]
        for k, v in ov.items():
            writer.writerow(["overview", k, v])

        writer.writerow([])
        writer.writerow(["DAILY"])
        writer.writerow(["date", "cost", "calls"])
        for row in payload["daily"]:
            writer.writerow([row["date"], row["cost"], row["calls"]])

        writer.writerow([])
        writer.writerow(["PROJECTS"])
        writer.writerow(["project", "cost", "calls", "sessions", "avg_cost_per_session"])
        for row in payload["projects"]:
            writer.writerow([row["project"], row["cost"], row["calls"], row["sessions"], row["avg_cost_per_session"]])

        writer.writerow([])
        writer.writerow(["MODELS"])
        writer.writerow(["model", "label", "cost", "calls", "input", "output", "cache_read"])
        for row in payload["models"]:
            writer.writerow([row["model"], row["label"], row["cost"], row["calls"],
                             row["input_tokens"], row["output_tokens"], row["cache_read"]])

        writer.writerow([])
        writer.writerow(["ACTIVITIES"])
        writer.writerow(["category", "cost", "calls", "edit_turns", "one_shot_rate_pct"])
        for row in payload["activities"]:
            writer.writerow([row["category"], row["cost"], row["calls"],
                             row["edit_turns"], row["one_shot_rate"]])

        writer.writerow([])
        writer.writerow(["CORE TOOLS"])
        writer.writerow(["tool", "count"])
        for row in payload["core_tools"]:
            writer.writerow([row["tool"], row["count"]])

        writer.writerow([])
        writer.writerow(["SHELL COMMANDS"])
        writer.writerow(["command", "count"])
        for row in payload["shell"]:
            writer.writerow([row["command"], row["count"]])

        writer.writerow([])
        writer.writerow(["MCP SERVERS"])
        writer.writerow(["server", "count"])
        for row in payload["mcp"]:
            writer.writerow([row["server"], row["count"]])

        writer.writerow([])
        writer.writerow(["TOP SESSIONS"])
        writer.writerow(["session_id", "title", "project", "cost", "calls"])
        for row in payload["top_sessions"]:
            writer.writerow([row["session_id"], row["title"], row["project_path"], row["cost"], row["calls"]])

        body = buf.getvalue().encode("utf-8")
        return (f"dashboard-{payload['period']}.csv", body, "text/csv")

    raise ValueError(f"Unknown format: {fmt}")
