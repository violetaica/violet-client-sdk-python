"""Response fixtures + SSE encoder.

A dependency-free utility that emits responses structurally identical to the
Messages API response shapes. Use
it to exercise the SDK without hitting the gateway, or as a mock server's body.

    from violet import fixtures, Message
    msg = Message(fixtures.message(text="hi"))
    assert msg.content[0].text == "hi"
"""
from __future__ import annotations

import json
from typing import Any, List, Optional

STOP_REASONS = ("end_turn", "max_tokens", "stop_sequence", "tool_use", "pause_turn", "refusal")

_ERROR_TYPES = {
    400: "invalid_request_error", 401: "authentication_error", 403: "permission_error",
    404: "not_found_error", 413: "request_too_large", 429: "rate_limit_error",
    500: "api_error", 529: "overloaded_error",
}


# ── Building blocks ──────────────────────────────────────────────────────────

def text_block(text: str) -> dict:
    return {"type": "text", "text": text, "citations": None}


def tool_use_block(*, id: str = "toolu_fixture0000000000001",
                   name: str = "calculator", input: Optional[dict] = None) -> dict:
    return {"type": "tool_use", "id": id, "name": name,
            "input": input if input is not None else {"operation": "add", "a": 1, "b": 2}}


def usage(input_tokens: int = 12, output_tokens: int = 6,
          cache_creation: int = 0, cache_read: int = 0) -> dict:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": cache_creation,
        "cache_read_input_tokens": cache_read,
    }


# ── Non-streaming Message ────────────────────────────────────────────────────

def message(
    *,
    id: str = "msg_fixture0000000000000001",
    model: str = "claude-opus-4-8",
    content: Optional[List[dict]] = None,
    text: Optional[str] = "Hello world",
    tool_use: Optional[dict] = None,
    stop_reason: str = "end_turn",
    stop_sequence: Optional[str] = None,
    input_tokens: int = 12,
    output_tokens: int = 6,
    refusal: bool = False,
) -> dict:
    if refusal:
        return {
            "id": id, "type": "message", "role": "assistant", "model": model,
            "content": [], "stop_reason": "refusal", "stop_sequence": None,
            "usage": usage(input_tokens, 0),
            "stop_details": {"type": "refusal", "category": "cyber", "explanation": "declined"},
        }
    if content is None:
        content = []
        if text is not None:
            content.append(text_block(text))
        if tool_use is not None:
            content.append(tool_use_block(**tool_use))
            stop_reason = "tool_use"
    return {
        "id": id, "type": "message", "role": "assistant", "model": model,
        "content": content, "stop_reason": stop_reason, "stop_sequence": stop_sequence,
        "usage": usage(input_tokens, output_tokens),
    }


# ── Other unary shapes ───────────────────────────────────────────────────────

def count_tokens(input_tokens: int = 2095) -> dict:
    return {"input_tokens": input_tokens}


def error(status: int = 400, message: str = "bad request",
          request_id: str = "req_fixture0000000000001") -> dict:
    return {
        "type": "error",
        "error": {"type": _ERROR_TYPES.get(status, "api_error"), "message": message},
        "request_id": request_id,
    }


# ── SSE encoder ──────────────────────────────────────────────────────────────

def sse_frame(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def sse_message(
    *,
    id: str = "msg_fixture0000000000000002",
    model: str = "claude-opus-4-8",
    text: str = "hi there",
    chunks: Optional[List[str]] = None,
    tool_use: Optional[dict] = None,
    input_tokens: int = 25,
    output_tokens: Optional[int] = None,
    include_ping: bool = True,
) -> bytes:
    """Encode a full streamed message as SSE bytes (the exact event
    sequence). Feed the bytes to ``MessageStream`` / the SDK to round-trip."""
    frames: List[str] = []
    frames.append(sse_frame("message_start", {
        "type": "message_start",
        "message": {
            "id": id, "type": "message", "role": "assistant", "model": model,
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": 1},
        },
    }))
    if include_ping:
        frames.append(sse_frame("ping", {"type": "ping"}))

    if tool_use is None:
        frames.append(sse_frame("content_block_start", {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "text", "text": ""},
        }))
        for part in (chunks if chunks is not None else _split(text)):
            frames.append(sse_frame("content_block_delta", {
                "type": "content_block_delta", "index": 0,
                "delta": {"type": "text_delta", "text": part},
            }))
        frames.append(sse_frame("content_block_stop", {"type": "content_block_stop", "index": 0}))
        stop_reason = "end_turn"
        out = output_tokens if output_tokens is not None else max(1, len(text) // 3)
    else:
        tid = tool_use.get("id", "toolu_fixture0000000000002")
        name = tool_use.get("name", "calculator")
        inp = tool_use.get("input", {"operation": "add", "a": 1, "b": 2})
        frames.append(sse_frame("content_block_start", {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "tool_use", "id": tid, "name": name, "input": {}},
        }))
        for part in _split(json.dumps(inp), 4):
            frames.append(sse_frame("content_block_delta", {
                "type": "content_block_delta", "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": part},
            }))
        frames.append(sse_frame("content_block_stop", {"type": "content_block_stop", "index": 0}))
        stop_reason = "tool_use"
        out = output_tokens if output_tokens is not None else 5

    frames.append(sse_frame("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        "usage": {"output_tokens": out},
    }))
    frames.append(sse_frame("message_stop", {"type": "message_stop"}))
    return "".join(frames).encode()


def _split(s: str, n: int = 3) -> List[str]:
    return [s[i:i + n] for i in range(0, len(s), n)] or [""]
