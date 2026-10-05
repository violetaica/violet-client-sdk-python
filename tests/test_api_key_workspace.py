"""Workspace resolution for workspace API keys (IMPL-20 / LLD-20).

Narrow-scoped keys must not call ``POST /api/auth/login`` (that required ``*``
before the gateway opened login to any authenticated key). The SDK omits
``X-Workspace-Id`` and lets the gateway inject the key's bound workspace.
"""
from __future__ import annotations

from violet import Violet


def test_ensure_workspace_skips_login_for_api_key(monkeypatch) -> None:
    calls: list[tuple] = []

    client = Violet(api_key="vio_sk_live_0123456789abcdef01234567_secret")

    def _boom(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("API-key clients must not call /api/auth/login")

    monkeypatch.setattr(client._transport, "request", _boom)

    assert client._auth.uses_api_key
    assert client._ensure_workspace() is None
    assert calls == []


def test_ensure_workspace_uses_explicit_workspace_with_api_key() -> None:
    client = Violet(
        api_key="vio_sk_live_0123456789abcdef01234567_secret",
        workspace_id="ws_explicit",
    )
    assert client._ensure_workspace() == "ws_explicit"
