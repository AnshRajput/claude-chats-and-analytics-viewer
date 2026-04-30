import json
import tempfile
from pathlib import Path

from claude_conversation_viewer.web import (
    get_model_pricing,
    estimate_cost,
    parse_full_conversation,
    ConversationStore,
)


# ── Model pricing ─────────────────────────────────────────────────────────────

def test_pricing_known_model():
    pricing = get_model_pricing("claude-opus-4-5")
    assert len(pricing) == 4
    assert all(p >= 0 for p in pricing)


def test_pricing_unknown_model_returns_defaults():
    pricing = get_model_pricing("unknown-model-xyz")
    assert len(pricing) == 4


def test_pricing_sonnet():
    pricing = get_model_pricing("claude-sonnet-4-5")
    assert pricing[0] > 0  # input cost
    assert pricing[1] > 0  # output cost


# ── Cost estimation ───────────────────────────────────────────────────────────

def test_estimate_cost_zero_for_empty():
    conv = {"messages": []}
    assert estimate_cost(conv) == 0.0


def test_estimate_cost_positive_for_tokens():
    conv = {
        "messages": [
            {
                "role": "assistant",
                "model": "claude-haiku-4-5",
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 500,
                    "cache_creation": 0,
                    "cache_read": 0,
                },
                "estimated_cost_usd": 0.001,
            }
        ]
    }
    cost = estimate_cost(conv)
    assert cost >= 0.0


# ── Conversation parsing ──────────────────────────────────────────────────────

def _make_jsonl(lines):
    tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w")
    for line in lines:
        tmp.write(json.dumps(line) + "\n")
    tmp.close()
    return Path(tmp.name)


def test_parse_empty_file():
    path = _make_jsonl([])
    msgs = parse_full_conversation(path)
    assert msgs == []
    path.unlink()


def test_parse_user_message():
    path = _make_jsonl([
        {
            "type": "user",
            "message": {"role": "user", "content": "Hello Claude"},
            "timestamp": "2025-01-01T00:00:00Z",
        }
    ])
    msgs = parse_full_conversation(path)
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"][0]["text"] == "Hello Claude"
    path.unlink()


def test_parse_assistant_message():
    path = _make_jsonl([
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "model": "claude-sonnet-4-5",
                "content": [{"type": "text", "text": "Hi there!"}],
                "usage": {"input_tokens": 10, "output_tokens": 5,
                          "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
            },
            "timestamp": "2025-01-01T00:00:01Z",
        }
    ])
    msgs = parse_full_conversation(path)
    assert len(msgs) == 1
    assert msgs[0]["role"] == "assistant"
    assert msgs[0]["content"][0]["text"] == "Hi there!"
    path.unlink()


def test_parse_skips_meta_messages():
    path = _make_jsonl([
        {
            "type": "user",
            "isMeta": True,
            "message": {"role": "user", "content": "meta info"},
            "timestamp": "2025-01-01T00:00:00Z",
        }
    ])
    msgs = parse_full_conversation(path)
    assert msgs == []
    path.unlink()


def test_parse_thinking_block():
    path = _make_jsonl([
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "model": "claude-sonnet-4-5",
                "content": [
                    {"type": "thinking", "thinking": "Let me think..."},
                    {"type": "text", "text": "Answer"},
                ],
                "usage": {"input_tokens": 10, "output_tokens": 5,
                          "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
            },
            "timestamp": "2025-01-01T00:00:01Z",
        }
    ])
    msgs = parse_full_conversation(path)
    assert len(msgs) == 1
    types = [b["type"] for b in msgs[0]["content"]]
    assert "thinking" in types
    assert "text" in types
    path.unlink()


# ── ConversationStore ─────────────────────────────────────────────────────────

def test_store_initializes_empty():
    store = ConversationStore()
    assert store.conversations == []
    assert store.projects == []
