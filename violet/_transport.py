"""Low-level HTTP transport: header assembly, retries, error mapping, raw SSE.

Standard-library only (`urllib`). The Violet-specific bits live here so the
resource classes stay focused on the public surface:
  * Bearer token from the Auth0 authenticator (no API key)
  * X-Workspace-Id / X-Surface tenant headers
  * a browser-like User-Agent (the gateway is behind Cloudflare bot protection;
    the default urllib agent gets 403 error 1010)
"""
from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Iterator, Optional, Tuple

from .errors import APIConnectionError, APITimeoutError, error_from_status

_RETRY_STATUSES = {408, 409, 429}


def _request_id(headers) -> Optional[str]:
    try:
        return headers.get("request-id") or headers.get("x-request-id")
    except Exception:
        return None


class Transport:
    def __init__(
        self,
        *,
        base_url: str,
        token_provider: Callable[[], str],
        user_agent: str,
        surface: str,
        timeout: float,
        max_retries: int,
    ):
        self.base_url = base_url.rstrip("/")
        self._token = token_provider
        self.user_agent = user_agent
        self.surface = surface
        self.timeout = timeout
        self.max_retries = max_retries

    # ── Headers ──────────────────────────────────────────────────────────────

    def headers(self, *, workspace_id: Optional[str], json_body: bool, stream: bool) -> dict:
        h = {
            "Authorization": f"Bearer {self._token()}",
            "X-Surface": self.surface,
            "User-Agent": self.user_agent,  # avoid Cloudflare bot block (error 1010)
            "Accept": "text/event-stream" if stream else "application/json",
        }
        if json_body:
            h["Content-Type"] = "application/json"
        if workspace_id:
            h["X-Workspace-Id"] = workspace_id
        return h

    # ── Unary request (with retries) ─────────────────────────────────────────

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        params: Optional[dict] = None,
        workspace_id: Optional[str] = None,
    ) -> Tuple[Any, Optional[str]]:
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        headers = self.headers(workspace_id=workspace_id, json_body=body is not None, stream=False)

        attempt = 0
        while True:
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    raw = r.read()
                    parsed = json.loads(raw) if raw else {}
                    return parsed, _request_id(r.headers)
            except urllib.error.HTTPError as e:
                status = e.code
                rid = _request_id(e.headers)
                err_body = _read_error_body(e)
                if status in _RETRY_STATUSES or status >= 500:
                    if attempt < self.max_retries:
                        time.sleep(_backoff(attempt, e.headers))
                        attempt += 1
                        continue
                raise error_from_status(status, err_body, rid) from None
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < self.max_retries:
                    time.sleep(_backoff(attempt, None))
                    attempt += 1
                    continue
                if isinstance(e, TimeoutError) or "timed out" in str(e).lower():
                    raise APITimeoutError(f"request to {url} timed out: {e}") from e
                raise APIConnectionError(f"could not reach {url}: {e}") from e

    # ── Streaming request (opens the connection; caller reads SSE) ───────────

    def open_stream(
        self,
        method: str,
        path: str,
        *,
        body: Any,
        workspace_id: Optional[str],
    ):
        url = f"{self.base_url}{path}"
        headers = self.headers(workspace_id=workspace_id, json_body=True, stream=True)
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(), headers=headers, method=method
        )
        try:
            return urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            raise error_from_status(e.code, _read_error_body(e), _request_id(e.headers)) from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise APIConnectionError(f"could not reach {url}: {e}") from e


def _read_error_body(e: urllib.error.HTTPError) -> Any:
    body_bytes = b""
    try:
        body_bytes = e.read() or b""
    except Exception:
        pass
    if not body_bytes:
        return {}
    try:
        return json.loads(body_bytes)
    except ValueError:
        return {"error": body_bytes.decode("utf-8", "replace").strip()[:500]}


def _backoff(attempt: int, headers) -> float:
    # Honour Retry-After when present; else exponential backoff with jitter.
    if headers is not None:
        ra = headers.get("retry-after")
        if ra:
            try:
                return min(float(ra), 60.0)
            except ValueError:
                pass
    return min(0.5 * (2 ** attempt) + random.uniform(0, 0.25), 8.0)


# ── SSE frame parser ─────────────────────────────────────────────────────────

def sse_frames(resp) -> Iterator[dict]:
    """Yield one parsed JSON object per SSE frame (blank-line separated)."""
    data_lines = []
    for raw in resp:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if line == "":
            if data_lines:
                payload = "\n".join(data_lines)
                data_lines = []
                if payload.strip() == "[DONE]":
                    return
                try:
                    yield json.loads(payload)
                except json.JSONDecodeError:
                    yield {"_raw": payload}
            continue
        if line.startswith(":"):  # comment / keep-alive
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        # event:/id:/retry: ignored — the type is inside the JSON
    if data_lines:
        payload = "\n".join(data_lines)
        if payload.strip() != "[DONE]":
            try:
                yield json.loads(payload)
            except json.JSONDecodeError:
                yield {"_raw": payload}
