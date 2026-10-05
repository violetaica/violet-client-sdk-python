"""Native-async client — `AsyncViolet`, the async twin of sync `Violet`.

Unlike the sync `Violet` (stdlib-only `urllib`), the async path does real
non-blocking I/O via **aiohttp**, an OPTIONAL dependency: ``pip install
violet-sdk[async]``. aiohttp is imported lazily, so importing `violet` never
requires it — only constructing/using `AsyncViolet` does.

The rare Auth0 token resolution reuses the (sync) `Authenticator` via a thread
(`asyncio.to_thread`) — it's not the hot path; the message/stream calls are
genuinely async. Body-building, error mapping, SSE accumulation, and the typed
objects are shared with the sync stack.
"""
from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, List, Optional

from .auth import Authenticator
from .errors import APIConnectionError, APIError, APITimeoutError, VioletError, error_from_status
from ._streaming import accumulate_frame, text_delta_of
from .resources import _message_body, _resolve_schema, _unwrap
from .types import Event, Message, TokenCount, UsageReport

DEFAULT_BASE_URL = "https://api.violetai.ca"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_RETRY_STATUSES = {408, 409, 429}


def _require_aiohttp():
    try:
        import aiohttp  # noqa: F401
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise VioletError(
            "AsyncViolet needs aiohttp. Install it with: pip install violet-sdk[async]"
        ) from exc
    return aiohttp


def _backoff(attempt: int, headers) -> float:
    if headers is not None:
        ra = headers.get("retry-after")
        if ra:
            try:
                return min(float(ra), 60.0)
            except (TypeError, ValueError):
                pass
    return min(0.5 * (2 ** attempt) + random.uniform(0, 0.25), 8.0)


# ── Async SSE parser ──────────────────────────────────────────────────────────

async def async_sse_frames(byte_aiter: AsyncIterator[bytes]) -> AsyncIterator[dict]:
    """Yield one parsed JSON object per SSE frame from an async byte source."""
    buf = ""
    async for chunk in byte_aiter:
        buf += chunk.decode("utf-8", "replace") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
        while True:
            i2, i4 = buf.find("\n\n"), buf.find("\r\n\r\n")
            if i4 != -1 and (i2 == -1 or i4 < i2):
                frame, buf = buf[:i4], buf[i4 + 4:]
            elif i2 != -1:
                frame, buf = buf[:i2], buf[i2 + 2:]
            else:
                break
            done, objs = _parse_frame(frame)
            for obj in objs:
                yield obj
            if done:
                return
    _done, objs = _parse_frame(buf)
    for obj in objs:
        yield obj


def _parse_frame(frame: str):
    data_lines = []
    for line in frame.replace("\r", "").split("\n"):
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if not data_lines:
        return (False, [])
    payload = "\n".join(data_lines)
    if payload.strip() == "[DONE]":
        return (True, [])
    try:
        return (False, [json.loads(payload)])
    except json.JSONDecodeError:
        return (False, [{"_raw": payload}])


# ── Async stream helper ───────────────────────────────────────────────────────

class AsyncMessageStream:
    """Async mirror of `MessageStream`: `async for` over events, `text_stream`
    (async), and `await get_final_message()`. A single internal consumer is
    shared by all three. `opener` is an async callable returning
    ``(byte_aiterator, aclose_coro_or_None)``."""

    def __init__(self, opener):
        self._opener = opener
        self._acc: dict = {"content": []}
        self._gen = None

    def _ensure_gen(self):
        if self._gen is None:
            self._gen = self._events()
        return self._gen

    def __aiter__(self):
        return self._ensure_gen()

    async def _events(self):
        byte_aiter, aclose = await self._opener()
        try:
            async for frame in async_sse_frames(byte_aiter):
                accumulate_frame(self._acc, frame)
                yield Event(frame)
        finally:
            if aclose is not None:
                try:
                    await aclose()
                except Exception:
                    pass

    @property
    def text_stream(self):
        return self._text_stream()

    async def _text_stream(self):
        async for ev in self._ensure_gen():
            td = text_delta_of(ev._data)
            if td:
                yield td

    async def get_final_message(self) -> Message:
        async for _ in self._ensure_gen():
            pass
        return Message(self._acc)


# ── The async client ──────────────────────────────────────────────────────────

class AsyncViolet:
    """Async Violet client (aiohttp-backed).

        async with AsyncViolet(workspace_id="ws_…") as client:
            msg = await client.messages.create(model=…, max_tokens=…, messages=[…])
            print(msg.content[0].text)

            async with client.messages.stream(model=…, messages=[…]) as stream:
                async for text in stream.text_stream:
                    ...
    """

    def __init__(
        self,
        *,
        auth: Optional[Authenticator] = None,
        auth_token: Optional[str] = None,
        api_key: Optional[str] = None,
        workspace_id: Optional[str] = None,
        base_url: Optional[str] = None,
        surface: str = "sdk",
        timeout: float = 60.0,
        max_retries: int = 2,
        user_agent: Optional[str] = None,
        raw_passthrough: bool = False,
    ):
        import os

        self._auth = auth or Authenticator()
        resolved_key = api_key or os.environ.get("VIOLET_API_KEY")
        if resolved_key:
            self._auth._api_key = resolved_key
        elif auth_token:
            self._auth._pre = auth_token
        self.base_url = (base_url or os.environ.get("MESSAGES_API_URL", DEFAULT_BASE_URL)).rstrip("/")
        self._workspace_id = (
            workspace_id
            or os.environ.get("WORKSPACE_ID")
            or os.environ.get("VIOLET_WORKSPACE_ID")
        )
        self.surface = surface
        self.timeout = timeout
        self.max_retries = max_retries
        self.user_agent = user_agent or os.environ.get("VIOLET_USER_AGENT", DEFAULT_USER_AGENT)
        self.raw_passthrough = raw_passthrough
        self._session = None  # lazily created aiohttp.ClientSession

        self.messages = _AsyncMessages(self)
        self.auth = _AsyncAuth(self)
        self.usage = _AsyncUsage(self)

    async def __aenter__(self) -> "AsyncViolet":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    # ── transport ─────────────────────────────────────────────────────────────

    async def _ensure_session(self):
        if self._session is None:
            aiohttp = _require_aiohttp()
            self._session = aiohttp.ClientSession()
        return self._session

    async def _token(self) -> str:
        return await asyncio.to_thread(self._auth.token)

    def _headers(self, *, workspace_id: Optional[str], json_body: bool, stream: bool, token: str) -> dict:
        h = {
            "Authorization": f"Bearer {token}",
            "X-Surface": self.surface,
            "User-Agent": self.user_agent,  # avoid Cloudflare bot block (error 1010)
            "Accept": "text/event-stream" if stream else "application/json",
        }
        if json_body:
            h["Content-Type"] = "application/json"
        if workspace_id:
            h["X-Workspace-Id"] = workspace_id
        return h

    @staticmethod
    def _request_id(headers) -> Optional[str]:
        return headers.get("request-id") or headers.get("x-request-id")

    @staticmethod
    async def _read_error_body(resp) -> Any:
        try:
            text = await resp.text()
        except Exception:
            return {}
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"error": text.strip()[:500]}

    async def request(self, method: str, path: str, *, body=None, params=None,
                      workspace_id=None, need_workspace=False):
        aiohttp = _require_aiohttp()
        session = await self._ensure_session()
        url = f"{self.base_url}{path}"
        ws = (workspace_id or await self.ensure_workspace()) if need_workspace else None
        payload = json.dumps(body) if body is not None else None
        timeout = aiohttp.ClientTimeout(total=self.timeout)

        attempt = 0
        while True:
            token = await self._token()
            headers = self._headers(workspace_id=ws, json_body=body is not None, stream=False, token=token)
            try:
                async with session.request(
                    method, url, data=payload, headers=headers, params=params, timeout=timeout
                ) as resp:
                    rid = self._request_id(resp.headers)
                    if resp.status < 400:
                        raw = await resp.read()
                        data = json.loads(raw) if raw else {}
                        return data, rid
                    status = resp.status
                    err_body = await self._read_error_body(resp)
                    if (status in _RETRY_STATUSES or status >= 500) and attempt < self.max_retries:
                        await asyncio.sleep(_backoff(attempt, resp.headers))
                        attempt += 1
                        continue
                    raise error_from_status(status, err_body, rid)
            except asyncio.TimeoutError as exc:
                if attempt < self.max_retries:
                    await asyncio.sleep(_backoff(attempt, None))
                    attempt += 1
                    continue
                raise APITimeoutError(f"request to {url} timed out") from exc
            except aiohttp.ClientError as exc:
                if attempt < self.max_retries:
                    await asyncio.sleep(_backoff(attempt, None))
                    attempt += 1
                    continue
                raise APIConnectionError(f"could not reach {url}: {exc}") from exc

    async def _open_stream(self, body, workspace_id=None):
        """Return ``(byte_aiterator, aclose)`` for an SSE response."""
        aiohttp = _require_aiohttp()
        session = await self._ensure_session()
        ws = workspace_id or await self.ensure_workspace()
        token = await self._token()
        url = f"{self.base_url}/api/v1/messages"
        headers = self._headers(workspace_id=ws, json_body=True, stream=True, token=token)
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        try:
            resp = await session.post(url, data=json.dumps(body), headers=headers, timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise APITimeoutError(f"request to {url} timed out") from exc
        except aiohttp.ClientError as exc:
            raise APIConnectionError(f"could not reach {url}: {exc}") from exc
        if resp.status >= 400:
            err = await self._read_error_body(resp)
            rid = self._request_id(resp.headers)
            resp.release()
            raise error_from_status(resp.status, err, rid)

        async def _aclose():
            try:
                await resp.release()
            except Exception:
                resp.close()

        return resp.content.iter_any(), _aclose

    async def stream_events(self, body, workspace_id=None) -> AsyncIterator[Event]:
        byte_aiter, aclose = await self._open_stream(body, workspace_id)
        try:
            async for frame in async_sse_frames(byte_aiter):
                yield Event(frame)
        finally:
            await aclose()

    # ── workspace + health ────────────────────────────────────────────────────

    async def ensure_workspace(self) -> Optional[str]:
        """Resolve workspace for ``X-Workspace-Id``.

        Auth0: ``POST /api/auth/login``. Workspace API key: return None so the
        gateway injects the key's bound workspace (LLD-20 T-20.1.3).
        """
        if self._workspace_id:
            return self._workspace_id
        if self._auth.uses_api_key:
            return None
        try:
            me, _ = await self.request("POST", "/api/auth/login", body={})
            me = me or {}
        except APIError as e:
            if e.status_code == 403 and e.code == "user_not_provisioned":
                raise VioletError(
                    "Signed in to Auth0, but this identity has no Violet account "
                    "(user_not_provisioned). Call client.auth.signup() to provision "
                    "it; a usable workspace also requires a paid subscription."
                ) from None
            raise
        ws = me.get("default_workspace") or me.get("defaultWorkspace")
        if isinstance(ws, dict):
            ws = ws.get("id") or ws.get("workspace_id") or ws.get("workspaceId")
        if isinstance(ws, str) and ws:
            self._workspace_id = ws
            return ws
        raise VioletError(
            "Signed in, but your account has no workspace (default_workspace is "
            "null). Create/subscribe a workspace in the Violet web app, or pass "
            "workspace_id=… / set WORKSPACE_ID."
        )

    async def health(self) -> bool:
        aiohttp = _require_aiohttp()
        session = await self._ensure_session()
        for path in ("/api/health", "/health", "/healthz", "/api/v1/health"):
            try:
                async with session.get(
                    f"{self.base_url}{path}",
                    headers={"User-Agent": self.user_agent, "Accept": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        return True
            except Exception:
                continue
        return False


# ── Async stream manager (async context manager) ──────────────────────────────

class _AsyncStreamManager:
    def __init__(self, client: AsyncViolet, body: dict, workspace_id):
        self._client = client
        self._body = body
        self._workspace_id = workspace_id
        self._stream: Optional[AsyncMessageStream] = None

    async def _opener(self):
        return await self._client._open_stream(self._body, self._workspace_id)

    async def __aenter__(self) -> AsyncMessageStream:
        self._stream = AsyncMessageStream(self._opener)
        return self._stream

    async def __aexit__(self, *exc) -> None:
        # Drain/close if the consumer didn't finish.
        if self._stream is not None and self._stream._gen is not None:
            try:
                await self._stream._gen.aclose()
            except Exception:
                pass

    def __aiter__(self):
        # allow `async for ev in client.messages.stream(...)` without `async with`
        return AsyncMessageStream(self._opener).__aiter__()


# ── Async resources ───────────────────────────────────────────────────────────

def _apply_raw(body: dict, per_call: Optional[bool], client: AsyncViolet) -> None:
    rp = per_call if per_call is not None else client.raw_passthrough
    if rp:
        body["raw_passthrough"] = True


class _AsyncMessages:
    def __init__(self, client: AsyncViolet):
        self._client = client

    async def create(self, *, model, max_tokens=1024, messages, system=None, tools=None,
                     tool_choice=None, stream=False, stop_sequences=None, metadata=None,
                     workspace_id=None, raw_passthrough=None, **extra):
        if stream:
            raise VioletError("use client.messages.stream(...) for streaming, not create(stream=True)")
        body = _message_body(model=model, max_tokens=max_tokens, messages=messages, system=system,
                             tools=tools, tool_choice=tool_choice, stop_sequences=stop_sequences,
                             metadata=metadata, stream=False, extra=extra)
        _apply_raw(body, raw_passthrough, self._client)
        data, rid = await self._client.request("POST", "/api/v1/messages", body=body,
                                               workspace_id=workspace_id, need_workspace=True)
        return Message(data, request_id=rid)

    def stream(self, *, model, max_tokens=1024, messages, system=None, tools=None,
               tool_choice=None, stop_sequences=None, metadata=None,
               workspace_id=None, raw_passthrough=None, **extra) -> _AsyncStreamManager:
        body = _message_body(model=model, max_tokens=max_tokens, messages=messages, system=system,
                             tools=tools, tool_choice=tool_choice, stop_sequences=stop_sequences,
                             metadata=metadata, stream=True, extra=extra)
        _apply_raw(body, raw_passthrough, self._client)
        return _AsyncStreamManager(self._client, body, workspace_id)

    async def count_tokens(self, *, model, messages, system=None, tools=None, workspace_id=None, **extra) -> TokenCount:
        body: dict = {"model": model, "messages": messages, **extra}
        if system is not None:
            body["system"] = system
        if tools is not None:
            body["tools"] = tools
        data, _ = await self._client.request("POST", "/api/v1/messages/count_tokens", body=body,
                                             workspace_id=workspace_id, need_workspace=True)
        return TokenCount(data if isinstance(data, dict) else {})

    async def parse(self, *, output_format, **kwargs) -> Message:
        schema, model_cls = _resolve_schema(output_format)
        kwargs.setdefault("max_tokens", 1024)
        kwargs["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
        msg = await self.create(**kwargs)
        text = "".join(b.text for b in msg.content if b.type == "text")
        try:
            parsed = json.loads(text) if text else None
        except json.JSONDecodeError:
            parsed = None
        if parsed is not None and model_cls is not None:
            validate = getattr(model_cls, "model_validate", None)
            parsed = validate(parsed) if validate else model_cls(**parsed)
        object.__setattr__(msg, "parsed_output", parsed)
        return msg


class _AsyncAuth:
    def __init__(self, client: AsyncViolet):
        self._client = client

    async def login(self) -> dict:
        data, _ = await self._client.request("POST", "/api/auth/login", body={})
        return data

    async def signup(self) -> dict:
        data, _ = await self._client.request("POST", "/api/auth/signup", body={})
        return data

    async def profile(self) -> dict:
        data, _ = await self._client.request("GET", "/api/auth/profile")
        return data

    async def logout(self, *, revoke=True, browser=False, return_to=None) -> None:
        await asyncio.to_thread(self._client._auth.logout, revoke=revoke, browser=browser, return_to=return_to)
        self._client._workspace_id = None


class _AsyncUsage:
    def __init__(self, client: AsyncViolet):
        self._client = client

    async def get(self, *, workspace_id=None) -> UsageReport:
        data, _ = await self._client.request("GET", "/api/v1/usage/me",
                                             workspace_id=workspace_id, need_workspace=True)
        return UsageReport(_unwrap(data))
