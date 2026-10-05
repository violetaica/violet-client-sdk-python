"""Structural-diff test: validate fixtures against the documented
response shapes, AND prove the SDK round-trips each one.

Two assertions per shape:
  1. shape — the fixture matches the documented structure (keys, types, enums,
     id-prefix patterns), ignoring concrete ids / timestamps / token values.
  2. round-trip — the SDK's parsers consume it the way callers expect
     (`msg.content[0].text`, the stream `text_stream`/`get_final_message`, etc.).

Run with pytest, or directly:  python tests/test_shapes.py
"""
from __future__ import annotations

import io
import json

from violet import (
    InternalServerError, Message, PermissionDeniedError,
    RateLimitError, TokenCount, ToolUseBlock, fixtures,
)
from violet._streaming import MessageStream
from violet.errors import error_from_status

# ── Shape validators (the "documented shapes") ───────────────────────────────

_STOP_REASONS = {"end_turn", "max_tokens", "stop_sequence", "tool_use", "pause_turn", "refusal"}
_DELTA_TYPES = {"text_delta", "input_json_delta", "thinking_delta", "signature_delta"}
_EVENT_TYPES = {
    "message_start", "content_block_start", "content_block_delta",
    "content_block_stop", "message_delta", "message_stop", "ping", "error",
}
_USAGE_FIELDS = ("input_tokens", "output_tokens",
                 "cache_creation_input_tokens", "cache_read_input_tokens")


def _is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def assert_block(b: dict):
    t = b.get("type")
    assert t in {"text", "thinking", "redacted_thinking", "tool_use", "server_tool_use",
                 "web_search_tool_result", "bash_code_execution_tool_result"}, t
    if t == "text":
        assert isinstance(b["text"], str)
    elif t == "thinking":
        assert isinstance(b["thinking"], str) and isinstance(b["signature"], str)
    elif t == "tool_use":
        assert b["id"].startswith("toolu_")
        assert isinstance(b["name"], str) and isinstance(b["input"], dict)


def assert_message_shape(m: dict):
    assert m["id"].startswith("msg_"), m["id"]
    assert m["type"] == "message" and m["role"] == "assistant"
    assert isinstance(m["model"], str)
    assert isinstance(m["content"], list), "content must always be an array"
    assert m["stop_reason"] in _STOP_REASONS, m["stop_reason"]
    assert m["stop_sequence"] is None or isinstance(m["stop_sequence"], str)
    u = m["usage"]
    for f in _USAGE_FIELDS:
        assert _is_int(u[f]), (f, u.get(f))
    for b in m["content"]:
        assert_block(b)
    if m["stop_reason"] == "refusal":
        assert m["stop_details"]["type"] == "refusal"


def assert_event_shape(e: dict):
    t = e.get("type")
    assert t in _EVENT_TYPES, t
    if t == "message_start":
        msg = e["message"]
        assert msg["id"].startswith("msg_") and msg["type"] == "message"
        assert _is_int(msg["usage"]["input_tokens"])
    elif t == "content_block_start":
        assert _is_int(e["index"]) and "type" in e["content_block"]
    elif t == "content_block_delta":
        assert _is_int(e["index"]) and e["delta"]["type"] in _DELTA_TYPES
    elif t == "content_block_stop":
        assert _is_int(e["index"])
    elif t == "message_delta":
        assert "delta" in e and _is_int(e["usage"]["output_tokens"])


def assert_error_shape(b: dict):
    assert b["type"] == "error"
    assert isinstance(b["error"]["type"], str) and isinstance(b["error"]["message"], str)


# ── Tests: shape + round-trip ────────────────────────────────────────────────

def test_message_text():
    m = fixtures.message(text="Hello world")
    assert_message_shape(m)
    msg = Message(m, request_id="req_1")
    assert msg.content[0].text == "Hello world"
    assert msg.usage.input_tokens == 12 and msg.usage.output_tokens == 6
    assert msg.usage.cache_read_input_tokens == 0
    assert msg.stop_reason == "end_turn" and msg.stop_sequence is None
    assert msg._request_id == "req_1"


def test_message_tool_use():
    m = fixtures.message(text=None, tool_use={"name": "calculator", "input": {"a": 7}})
    assert_message_shape(m)
    msg = Message(m)
    tu = msg.content[0]
    assert isinstance(tu, ToolUseBlock) and tu.name == "calculator" and tu.input["a"] == 7
    assert msg.stop_reason == "tool_use"


def test_message_refusal():
    m = fixtures.message(refusal=True)
    assert_message_shape(m)
    assert Message(m).stop_reason == "refusal"


def test_stream_text():
    raw = fixtures.sse_message(text="hi there")
    # every frame matches the event shape
    for ev in _frames(raw):
        assert_event_shape(ev)
    types = [ev["type"] for ev in _frames(raw)]
    for required in ("message_start", "content_block_start", "content_block_delta",
                     "content_block_stop", "message_delta", "message_stop"):
        assert required in types, (required, types)
    assert types[-1] == "message_stop"
    # round-trip through the SDK stream helper
    s = MessageStream(io.BytesIO(raw))
    assert "".join(s.text_stream) == "hi there"
    fm = s.get_final_message()
    assert fm.content[0].text == "hi there"
    assert fm.stop_reason == "end_turn"
    assert fm.usage.input_tokens == 25 and fm.usage.output_tokens > 0


def test_stream_tool_use():
    raw = fixtures.sse_message(tool_use={"name": "calculator", "input": {"operation": "multiply", "a": 17, "b": 23}})
    for ev in _frames(raw):
        assert_event_shape(ev)
    s = MessageStream(io.BytesIO(raw))
    for _ in s:
        pass
    tu = s.get_final_message().content[0]
    assert isinstance(tu, ToolUseBlock) and tu.input["a"] == 17 and tu.input["b"] == 23
    assert s.get_final_message().stop_reason == "tool_use"


def test_count_tokens():
    d = fixtures.count_tokens(2095)
    assert _is_int(d["input_tokens"]) and d["input_tokens"] > 0
    assert TokenCount(d).input_tokens == 2095


def test_errors_map_to_status_classes():
    for status, cls in [(403, PermissionDeniedError), (429, RateLimitError), (503, InternalServerError)]:
        body = fixtures.error(status)
        assert_error_shape(body)
        e = error_from_status(status, body, "req_x")
        assert isinstance(e, cls) and e.status_code == status
        assert e.type == body["error"]["type"]


# ── helpers / runner ─────────────────────────────────────────────────────────

def _frames(raw: bytes):
    out, data_lines = [], []
    for line in raw.decode().splitlines():
        if line == "":
            if data_lines:
                out.append(json.loads("\n".join(data_lines)))
                data_lines = []
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    return out


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} shape/round-trip tests passed")
