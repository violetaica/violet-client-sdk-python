"""Response objects — attribute-accessible, dependency-free.

Designed for ergonomic access (`msg.content[0].text`,
`msg.usage.input_tokens`, `isinstance(block, TextBlock)`) without pulling in a
dependency. Each wraps the raw JSON dict and exposes its keys as attributes;
nested dicts/lists are wrapped lazily so `event.delta.text` works.
"""
from __future__ import annotations

import json
from typing import Any, List, Optional


def _wrap(v: Any) -> Any:
    if isinstance(v, dict):
        return APIObject(v)
    if isinstance(v, list):
        return [_wrap(x) for x in v]
    return v


class APIObject:
    """Attribute-accessible wrapper over a JSON dict."""

    def __init__(self, data: Optional[dict] = None):
        object.__setattr__(self, "_data", dict(data or {}))

    def __getattr__(self, name: str) -> Any:
        # Private/dunder misses raise normally (and avoid recursion on _data).
        if name.startswith("_"):
            raise AttributeError(name)
        data = object.__getattribute__(self, "_data")
        if name in data:
            return _wrap(data[name])
        raise AttributeError(name)

    def get(self, name: str, default: Any = None) -> Any:
        return _wrap(self._data.get(name, default))

    # Dict-like access too, so e.g. `tool_use.input["a"]` and `"a" in block.input`
    # work (`input` is a plain dict). Use `.to_dict()` for a
    # real dict (json.dumps / **kwargs).
    def __getitem__(self, key: str) -> Any:
        return _wrap(self._data[key])

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def keys(self):
        return self._data.keys()

    def items(self):
        return [(k, _wrap(v)) for k, v in self._data.items()]

    def to_dict(self) -> dict:
        return dict(self._data)

    def to_json(self, indent: Optional[int] = None) -> str:
        return json.dumps(self._data, indent=indent)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._data!r})"


# ── Content blocks (response side) ─────────────────────────────────────────

class ContentBlock(APIObject):
    """Unknown/other block — base type. Has `.type`."""


class TextBlock(ContentBlock):
    """`.type == "text"`; `.text`."""


class ThinkingBlock(ContentBlock):
    """`.type == "thinking"`; `.thinking`, `.signature`."""


class ToolUseBlock(ContentBlock):
    """`.type == "tool_use"`; `.id` (toolu_…), `.name`, `.input`."""


_BLOCK_CLASSES = {
    "text": TextBlock,
    "thinking": ThinkingBlock,
    "tool_use": ToolUseBlock,
}


def make_block(d: dict) -> ContentBlock:
    return _BLOCK_CLASSES.get(d.get("type"), ContentBlock)(d)


# ── Usage ───────────────────────────────────────────────────────────────────

class Usage(APIObject):
    """Token counts. Missing cache fields read as 0."""

    _ZEROABLE = (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    )

    def __getattr__(self, name: str) -> Any:
        if name in self._ZEROABLE:
            return self._data.get(name, 0)
        return super().__getattr__(name)


# ── Message ─────────────────────────────────────────────────────────────────

class Message(APIObject):
    """A `POST /api/v1/messages` response.

    `.id`, `.type`, `.role`, `.model`, `.stop_reason`, `.stop_sequence`,
    `.content` (list of typed blocks), `.usage`, and `._request_id`.
    """

    def __init__(self, data: dict, request_id: Optional[str] = None):
        super().__init__(data)
        object.__setattr__(self, "content", [make_block(b) for b in (data.get("content") or [])])
        object.__setattr__(self, "usage", Usage(data.get("usage") or {}))
        object.__setattr__(self, "_request_id", request_id)
        object.__setattr__(self, "parsed_output", None)  # populated by messages.parse()

    def text(self) -> str:
        """Convenience: concatenate all text blocks (Violet helper,
        but handy — the canonical access is `msg.content[0].text`)."""
        return "".join(b.text for b in self.content if b.type == "text")


# ── Misc ────────────────────────────────────────────────────────────────────

class TokenCount(APIObject):
    """`.input_tokens`."""

    def __getattr__(self, name: str) -> Any:
        if name == "input_tokens":
            n = self._data.get("input_tokens")
            if n is None:
                n = (self._data.get("data") or {}).get("input_tokens", 0)
            return n or 0
        return super().__getattr__(name)


class Event(APIObject):
    """A streamed SSE event — `.type` plus event-specific fields
    (`.message`, `.delta`, `.content_block`, `.usage`, `.index`)."""


class UsageReport(APIObject):
    """`GET /api/v1/usage/me` — the workspace's **monthly billing snapshot**
    (USD spend + limits + activity), from the workspace billing snapshot.

    This is a Violet extension (monthly USD snapshot, not per-request tokens), and it
    covers the current billing period (anchored to signup), not an arbitrary
    window. Every raw field is attribute-accessible; these are convenience
    aliases (None when the field is absent).
    """

    @property
    def usage_monthly_usd(self):
        return self._data.get("usage_monthly_usd")

    @property
    def limit_monthly_usd(self):
        return self._data.get("limit_monthly_usd")

    @property
    def limit_remaining_usd(self):
        return self._data.get("limit_remaining_usd")

    @property
    def resets_at(self):
        return self._data.get("resets_at")

    @property
    def daily_activity(self):
        return self._data.get("daily_activity") or []

    @property
    def top_models(self):
        return self._data.get("top_models") or []
