"""Model pricing table and cost estimation.

Prices are USD per 1M tokens, as (input, output, cache_write, cache_read).
Covers every Claude and GPT family in current circulation plus fallbacks.
"""

from __future__ import annotations

from typing import Dict, Tuple

Pricing = Tuple[float, float, float, float]

MODEL_PRICING: Dict[str, Pricing] = {
    "claude-opus-4":     (15.00, 75.00, 18.75, 1.50),
    "claude-sonnet-4":   (3.00,  15.00, 3.75,  0.30),
    "claude-haiku-4":    (0.80,  4.00,  1.00,  0.08),
    "claude-3-7-sonnet": (3.00,  15.00, 3.75,  0.30),
    "claude-3-5-sonnet": (3.00,  15.00, 3.75,  0.30),
    "claude-3-5-haiku":  (0.80,  4.00,  1.00,  0.08),
    "claude-3-opus":     (15.00, 75.00, 18.75, 1.50),
    "claude-3-sonnet":   (3.00,  15.00, 3.75,  0.30),
    "claude-3-haiku":    (0.25,  1.25,  0.30,  0.03),
    "gpt-5":             (2.50,  10.00, 0.0,   0.25),
    "gpt-4o":            (2.50,  10.00, 0.0,   1.25),
    "gpt-4":             (30.00, 60.00, 0.0,   0.0),
}

DEFAULT_PRICING: Pricing = (3.00, 15.00, 3.75, 0.30)


def get_model_pricing(model_name: str) -> Pricing:
    if not model_name:
        return DEFAULT_PRICING
    lower = model_name.lower()
    for pattern, pricing in MODEL_PRICING.items():
        if pattern in lower:
            return pricing
    return DEFAULT_PRICING


def estimate_cost(conv: dict) -> float:
    """Cost for a full conversation metadata blob."""
    models = conv.get("models", [])
    pricing = get_model_pricing(models[0] if models else "")
    cost = (
        conv.get("total_input_tokens", 0) / 1_000_000 * pricing[0]
        + conv.get("total_output_tokens", 0) / 1_000_000 * pricing[1]
        + conv.get("total_cache_creation", 0) / 1_000_000 * pricing[2]
        + conv.get("total_cache_read", 0) / 1_000_000 * pricing[3]
    )
    return round(cost, 4)


def estimate_turn_cost(model: str, usage: dict) -> float:
    """Cost for a single assistant turn."""
    pricing = get_model_pricing(model)
    return round(
        usage.get("input_tokens", 0) / 1e6 * pricing[0]
        + usage.get("output_tokens", 0) / 1e6 * pricing[1]
        + usage.get("cache_creation", 0) / 1e6 * pricing[2]
        + usage.get("cache_read", 0) / 1e6 * pricing[3],
        6,
    )


def clean_model_name(model: str) -> str:
    """Short human label like 'Opus 4.5' from a raw model id."""
    if not model:
        return "unknown"
    m = model.lower()
    if "opus" in m:
        suffix = m.split("opus-")[-1].split("-")[0] if "opus-" in m else ""
        return "Opus" + (f" {suffix}" if suffix else "")
    if "sonnet" in m:
        suffix = m.split("sonnet-")[-1].split("-")[0] if "sonnet-" in m else ""
        return "Sonnet" + (f" {suffix}" if suffix else "")
    if "haiku" in m:
        suffix = m.split("haiku-")[-1].split("-")[0] if "haiku-" in m else ""
        return "Haiku" + (f" {suffix}" if suffix else "")
    return model
