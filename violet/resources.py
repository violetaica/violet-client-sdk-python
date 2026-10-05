"""Resource namespaces:
`client.messages.*`, plus Violet extensions `client.auth.*` and `client.usage.*`.
"""
from __future__ import annotations

from typing import Any, List, Optional

from ._streaming import MessageStreamManager
from .errors import APIError, VioletError
from .types import Event, Message, TokenCount, UsageReport


def _message_body(
    *, model, max_tokens, messages, system, tools, tool_choice, stop_sequences,
    metadata, stream, extra,
) -> dict:
    body: dict = {"model": model, "max_tokens": max_tokens, "messages": messages, **extra}
    if stream is not None:
        body["stream"] = stream
    if system is not None:
        body["system"] = system
    if tools is not None:
        body["tools"] = tools
    if tool_choice is not None:
        body["tool_choice"] = tool_choice
    if stop_sequences is not None:
        body["stop_sequences"] = stop_sequences
    if metadata is not None:
        body["metadata"] = metadata
    return body


class MessagesResource:
    def __init__(self, client):
        self._client = client

    def create(
        self,
        *,
        model: str,
        max_tokens: int = 1024,
        messages: List[dict],
        system: Optional[Any] = None,
        tools: Optional[List[dict]] = None,
        tool_choice: Optional[dict] = None,
        stream: bool = False,
        stop_sequences: Optional[List[str]] = None,
        metadata: Optional[dict] = None,
        workspace_id: Optional[str] = None,
        raw_passthrough: Optional[bool] = None,
        **extra: Any,
    ):
        """`POST /api/v1/messages`. Returns a `Message`, or — when `stream=True`
        — a low-level iterator of `Event`s (use `.stream(...)` for the helper)."""
        body = _message_body(
            model=model, max_tokens=max_tokens, messages=messages, system=system,
            tools=tools, tool_choice=tool_choice, stop_sequences=stop_sequences,
            metadata=metadata, stream=stream, extra=extra,
        )
        self._maybe_raw(body, raw_passthrough)
        if stream:
            return self._client._stream_events(body, workspace_id)
        data, rid = self._client._request(
            "POST", "/api/v1/messages", body=body, workspace_id=workspace_id, need_workspace=True
        )
        return Message(data, request_id=rid)

    def stream(
        self,
        *,
        model: str,
        max_tokens: int = 1024,
        messages: List[dict],
        system: Optional[Any] = None,
        tools: Optional[List[dict]] = None,
        tool_choice: Optional[dict] = None,
        stop_sequences: Optional[List[str]] = None,
        metadata: Optional[dict] = None,
        workspace_id: Optional[str] = None,
        raw_passthrough: Optional[bool] = None,
        **extra: Any,
    ) -> MessageStreamManager:
        """Context manager for `messages.stream(...)`:
        `with client.messages.stream(...) as s: for t in s.text_stream: ...`."""
        body = _message_body(
            model=model, max_tokens=max_tokens, messages=messages, system=system,
            tools=tools, tool_choice=tool_choice, stop_sequences=stop_sequences,
            metadata=metadata, stream=True, extra=extra,
        )
        self._maybe_raw(body, raw_passthrough)
        return MessageStreamManager(self._client, body, workspace_id)

    def _maybe_raw(self, body: dict, raw_passthrough: Optional[bool]) -> None:
        """Opt into the gateway's stateless raw-passthrough path (body flag the
        backend reads). Per-call override wins over the client default. On this
        path the gateway dictates the model — `model` is ignored server-side."""
        rp = raw_passthrough if raw_passthrough is not None else self._client.raw_passthrough
        if rp:
            body["raw_passthrough"] = True

    def count_tokens(
        self,
        *,
        model: str,
        messages: List[dict],
        system: Optional[Any] = None,
        tools: Optional[List[dict]] = None,
        workspace_id: Optional[str] = None,
        **extra: Any,
    ) -> TokenCount:
        body: dict = {"model": model, "messages": messages, **extra}
        if system is not None:
            body["system"] = system
        if tools is not None:
            body["tools"] = tools
        data, _ = self._client._request(
            "POST", "/api/v1/messages/count_tokens", body=body,
            workspace_id=workspace_id, need_workspace=True,
        )
        return TokenCount(data if isinstance(data, dict) else {})

    def parse(self, *, output_format: Any, **kwargs: Any) -> Message:
        """Constrain output to a JSON schema and
        return a `Message` whose `.parsed_output` is the parsed object.

        `output_format` may be a JSON-schema dict, or a type exposing
        `model_json_schema()` (pydantic) — in which case `.parsed_output` is an
        instance built via `model_validate`/constructor.
        """
        import json as _json

        schema, model_cls = _resolve_schema(output_format)
        kwargs.setdefault("max_tokens", 1024)
        kwargs["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
        msg = self.create(**kwargs)
        text = "".join(b.text for b in msg.content if b.type == "text")
        try:
            parsed = _json.loads(text) if text else None
        except _json.JSONDecodeError:
            parsed = None
        if parsed is not None and model_cls is not None:
            validate = getattr(model_cls, "model_validate", None)
            parsed = validate(parsed) if validate else model_cls(**parsed)
        object.__setattr__(msg, "parsed_output", parsed)
        return msg


class AuthResource:
    """Violet extension — Auth0 + provisioning."""

    def __init__(self, client):
        self._client = client

    def login(self) -> dict:
        data, _ = self._client._request("POST", "/api/auth/login", body={})
        return data

    def signup(self) -> dict:
        data, _ = self._client._request("POST", "/api/auth/signup", body={})
        return data

    def profile(self) -> dict:
        data, _ = self._client._request("GET", "/api/auth/profile")
        return data

    def logout(self, *, revoke: bool = True, browser: bool = False, return_to=None) -> None:
        self._client._auth.logout(revoke=revoke, browser=browser, return_to=return_to)
        self._client._workspace_id = None

    def force_login(self) -> str:
        return self._client._auth.login()


class UsageResource:
    """Violet extension — workspace metering."""

    def __init__(self, client):
        self._client = client

    def get(self, *, workspace_id: Optional[str] = None) -> UsageReport:
        """`GET /api/v1/usage/me` — the workspace's monthly billing snapshot
        (USD spend + limits + activity). Takes no time window — it's the current
        billing period, anchored to the workspace signup date."""
        data, _ = self._client._request(
            "GET", "/api/v1/usage/me", workspace_id=workspace_id, need_workspace=True,
        )
        return UsageReport(_unwrap(data))


# ── helpers ──────────────────────────────────────────────────────────────────

def _unwrap(body: Any) -> dict:
    if isinstance(body, dict) and "data" in body and len(body) == 1 and isinstance(body["data"], dict):
        return body["data"]
    return body if isinstance(body, dict) else {}


def _resolve_schema(output_format: Any):
    """Return (json_schema_dict, model_class_or_None)."""
    if isinstance(output_format, dict):
        return output_format, None
    gen = getattr(output_format, "model_json_schema", None)  # pydantic v2
    if callable(gen):
        return gen(), output_format
    schema = getattr(output_format, "__violet_schema__", None)
    if isinstance(schema, dict):
        return schema, output_format
    raise VioletError(
        "output_format must be a JSON-schema dict or a type exposing "
        "model_json_schema() / __violet_schema__"
    )
