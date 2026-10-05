"""Streaming helper for `messages.stream(...)`:
a context manager yielding a `MessageStream` with `.text_stream` and
`.get_final_message()`, plus iteration over typed events.
"""
from __future__ import annotations

import json
from typing import Iterator

from ._transport import sse_frames
from .types import Event, Message


def accumulate_frame(acc: dict, frame: dict) -> None:
    """Fold one SSE frame into the running message accumulator dict. Shared by
    the sync `MessageStream` and the async `AsyncMessageStream`."""
    t = frame.get("type")
    content = acc["content"]
    if t == "message_start":
        m = frame.get("message") or {}
        for k in ("id", "type", "role", "model"):
            if k in m:
                acc[k] = m[k]
        acc["usage"] = dict(m.get("usage") or {})
        acc["content"] = []
    elif t == "content_block_start":
        idx = frame.get("index", 0)
        cb = dict(frame.get("content_block") or {})
        if cb.get("type") == "text":
            cb.setdefault("text", "")
        if cb.get("type") == "thinking":
            cb.setdefault("thinking", "")
        while len(content) <= idx:
            content.append({})
        content[idx] = cb
    elif t == "content_block_delta":
        idx = frame.get("index", 0)
        if idx >= len(content):
            return
        block, delta = content[idx], (frame.get("delta") or {})
        dt = delta.get("type")
        if dt == "text_delta":
            block["text"] = block.get("text", "") + delta.get("text", "")
        elif dt == "thinking_delta":
            block["thinking"] = block.get("thinking", "") + delta.get("thinking", "")
        elif dt == "input_json_delta":
            block["_pj"] = block.get("_pj", "") + delta.get("partial_json", "")
    elif t == "content_block_stop":
        idx = frame.get("index", 0)
        if idx < len(content) and "_pj" in content[idx]:
            pj = content[idx].pop("_pj")
            try:
                content[idx]["input"] = json.loads(pj) if pj else {}
            except json.JSONDecodeError:
                content[idx]["input"] = {}
    elif t == "message_delta":
        d = frame.get("delta") or {}
        if "stop_reason" in d:
            acc["stop_reason"] = d["stop_reason"]
        if "stop_sequence" in d:
            acc["stop_sequence"] = d["stop_sequence"]
        u = frame.get("usage") or {}
        acc["usage"] = {**(acc.get("usage") or {}), **u}


def text_delta_of(frame: dict):
    """Return the text chunk of a content_block_delta(text_delta) frame, else None."""
    if frame.get("type") == "content_block_delta":
        delta = frame.get("delta") or {}
        if delta.get("type") == "text_delta":
            return delta.get("text", "")
    return None


class MessageStream:
    """Wraps an open SSE response. Iterate events, or use `.text_stream`, then
    `.get_final_message()` for the accumulated `Message`."""

    def __init__(self, response):
        self._response = response
        self._frames = sse_frames(response)
        self._acc = {"content": []}
        self._consumed = False

    # iteration over typed events
    def __iter__(self) -> Iterator[Event]:
        return self._events()

    def _events(self) -> Iterator[Event]:
        try:
            for frame in self._frames:
                self._accumulate(frame)
                yield Event(frame)
        finally:
            self._consumed = True
            self.close()

    @property
    def text_stream(self) -> Iterator[str]:
        for ev in self._events():
            if ev.type == "content_block_delta":
                delta = ev._data.get("delta") or {}
                if delta.get("type") == "text_delta":
                    yield delta.get("text", "")

    def get_final_message(self) -> Message:
        if not self._consumed:
            for _ in self._events():
                pass
        return Message(self._acc)

    def close(self) -> None:
        try:
            self._response.close()
        except Exception:
            pass

    # ── accumulation ─────────────────────────────────────────────────────────

    def _accumulate(self, frame: dict) -> None:
        accumulate_frame(self._acc, frame)


class MessageStreamManager:
    """Context manager returned by `client.messages.stream(...)`."""

    def __init__(self, client, body: dict, workspace_id):
        self._client = client
        self._body = body
        self._workspace_id = workspace_id
        self._stream = None

    def __enter__(self) -> MessageStream:
        ws = self._workspace_id or self._client._ensure_workspace()
        resp = self._client._transport.open_stream(
            "POST", "/api/v1/messages", body=self._body, workspace_id=ws
        )
        self._stream = MessageStream(resp)
        return self._stream

    def __exit__(self, *exc) -> None:
        if self._stream is not None:
            self._stream.close()

    # allow `for event in client.messages.stream(...)` without `with`
    def __iter__(self):
        return iter(self.__enter__())
