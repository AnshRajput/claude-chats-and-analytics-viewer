"""Subscription plan tracking.

Preset monthly prices (as publicly stated April 2026). These are plan PRICES,
not modelled token allowances — vendors don't publish precise consumer limits,
so the progress bar shows API-equivalent cost vs subscription price as a
break-even indicator, not a true quota meter.
"""

from __future__ import annotations

from typing import Dict

PRESETS: Dict[str, dict] = {
    "claude-max":   {"label": "Claude Max",   "monthly_usd": 200.0},
    "claude-pro":   {"label": "Claude Pro",   "monthly_usd": 20.0},
    "cursor-pro":   {"label": "Cursor Pro",   "monthly_usd": 20.0},
    "none":         {"label": "No plan",      "monthly_usd": 0.0},
}


def normalize_plan(preset: str, monthly_usd: float = 0.0) -> dict:
    preset = (preset or "none").lower()
    if preset in PRESETS and preset != "custom":
        base = PRESETS[preset]
        return {
            "preset": preset,
            "label": base["label"],
            "monthly_usd": base["monthly_usd"],
        }
    if preset == "custom":
        return {
            "preset": "custom",
            "label": f"Custom (${monthly_usd:.0f}/mo)",
            "monthly_usd": float(monthly_usd or 0.0),
        }
    return {"preset": "none", "label": "No plan", "monthly_usd": 0.0}


def list_presets() -> list:
    return [
        {"key": k, "label": v["label"], "monthly_usd": v["monthly_usd"]}
        for k, v in PRESETS.items()
        if k != "none"
    ]
