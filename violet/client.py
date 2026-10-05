"""The `Violet` client — messages API client (`client.messages.*`), with Auth0
(default) or workspace API key auth + workspace scoping. See README for the
full surface map.
"""
from __future__ import annotations

import os
from typing import Any, Iterator, Optional

from .auth import Authenticator
from ._transport import Transport, sse_frames
from .errors import APIError, VioletError
from .resources import AuthResource, MessagesResource, UsageResource
from .types import Event

DEFAULT_BASE_URL = "https://api.violetai.ca"

# The prod gateway is behind Cloudflare bot protection, which 403s (error 1010)
# the default "Python-urllib/x" agent. Send a browser-like UA.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class Violet:
    """Violet Messages API client.

        from violet import Violet
        # Interactive (Auth0) — default when no API key is set:
        client = Violet(workspace_id="ws_…")
        # CI / server (workspace API key):
        # client = Violet(api_key="vio_sk_…")  # or VIOLET_API_KEY
        msg = client.messages.create(
            model="Violet Test Model", max_tokens=64,  # gateway sets the model
            messages=[{"role": "user", "content": "Say hi"}],
        )
        print(msg.content[0].text)
    """

    def __init__(
        self,
        *,
        auth: Optional[Authenticator] = None,
        auth_token: Optional[str] = None,   # parity alias for a pre-supplied Auth0 JWT
        api_key: Optional[str] = None,      # workspace API key (vio_sk_…) — IMPL-20
        workspace_id: Optional[str] = None,
        base_url: Optional[str] = None,
        surface: str = "sdk",
        timeout: float = 60.0,
        max_retries: int = 2,
        user_agent: Optional[str] = None,
        raw_passthrough: bool = False,
    ):
        self._auth = auth or Authenticator()
        # Priority: explicit api_key > VIOLET_API_KEY (Authenticator) > auth_token > AUTH_TOKEN.
        resolved_key = api_key or os.environ.get("VIOLET_API_KEY")
        if resolved_key:
            self._auth._api_key = resolved_key
        elif auth_token:
            self._auth._pre = auth_token
        self.base_url = (base_url or os.environ.get("MESSAGES_API_URL", DEFAULT_BASE_URL)).rstrip("/")
        self._workspace_id = workspace_id or os.environ.get("WORKSPACE_ID") or os.environ.get("VIOLET_WORKSPACE_ID")
        self.surface = surface
        self.timeout = timeout
        self.max_retries = max_retries
        self.user_agent = user_agent or os.environ.get("VIOLET_USER_AGENT", DEFAULT_USER_AGENT)
        # When True, messages.create/stream ask the gateway for the stateless
        # single-call "raw passthrough" path (forwards the client's tools and
        # returns tool_use blocks for local execution). NOTE: on that path the
        # gateway dictates the model server-side — your `model` is ignored.
        self.raw_passthrough = raw_passthrough

        self._transport = Transport(
            base_url=self.base_url,
            token_provider=lambda: self._auth.token(),
            user_agent=self.user_agent,
            surface=self.surface,
            timeout=self.timeout,
            max_retries=self.max_retries,
        )

        # Resource namespaces
        self.messages = MessagesResource(self)
        # Violet extensions
        self.auth = AuthResource(self)
        self.usage = UsageResource(self)

    # ── Config helper ────────────────────────────────────────────────────────

    def with_options(self, *, timeout: Optional[float] = None,
                     max_retries: Optional[int] = None) -> "Violet":
        """Return a client with overridden settings."""
        return Violet(
            auth=self._auth,
            workspace_id=self._workspace_id,
            base_url=self.base_url,
            surface=self.surface,
            timeout=self.timeout if timeout is None else timeout,
            max_retries=self.max_retries if max_retries is None else max_retries,
            user_agent=self.user_agent,
            raw_passthrough=self.raw_passthrough,
        )

    # ── Violet extension ─────────────────────────────────────────────────────

    def health(self) -> bool:
        import urllib.request
        for path in ("/api/health", "/health", "/healthz", "/api/v1/health"):
            try:
                req = urllib.request.Request(
                    f"{self.base_url}{path}", method="GET",
                    headers={"User-Agent": self.user_agent, "Accept": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=10) as r:
                    if r.status == 200:
                        return True
            except Exception:
                continue
        return False

    # ── Internals used by resources ──────────────────────────────────────────

    def _request(self, method, path, *, body=None, params=None,
                 workspace_id=None, need_workspace=False):
        ws = (workspace_id or self._ensure_workspace()) if need_workspace else None
        return self._transport.request(method, path, body=body, params=params, workspace_id=ws)

    def _stream_events(self, body: dict, workspace_id) -> Iterator[Event]:
        ws = workspace_id or self._ensure_workspace()
        resp = self._transport.open_stream("POST", "/api/v1/messages", body=body, workspace_id=ws)
        try:
            for frame in sse_frames(resp):
                yield Event(frame)
        finally:
            try:
                resp.close()
            except Exception:
                pass

    def _ensure_workspace(self) -> Optional[str]:
        """Resolve workspace for ``X-Workspace-Id``.

        Auth0: ``POST /api/auth/login`` for the caller's default workspace.
        Workspace API key (``vio_sk_…``): return None so the header is omitted —
        the gateway injects the key's bound workspace (LLD-20 T-20.1.3). That
        keeps narrow scopes (``messages:write`` / ``usage:read``) working
        without requiring ``*`` for login.
        """
        if self._workspace_id:
            return self._workspace_id
        # Key is workspace-bound — omit header; server fills from the key.
        if self._auth.uses_api_key:
            return None
        try:
            me, _ = self._transport.request("POST", "/api/auth/login", body={})
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
            return self._workspace_id
        raise VioletError(
            "Signed in, but your account has no workspace (default_workspace is "
            "null). Create/subscribe a workspace in the Violet web app, or pass "
            "workspace_id=… / set WORKSPACE_ID."
        )


# AsyncViolet now lives in violet/_async.py — a native-async, aiohttp-backed
# client (optional `[async]` extra), not a thread wrapper.
