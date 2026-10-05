"""Authentication for the Violet Messages API.

Two credential shapes are supported (dual auth):

  * **Workspace API key** (``api_key=`` / ``VIOLET_API_KEY``) — opaque
    ``vio_sk_…`` secret for CI, servers, and SDKs. Used as
    ``Authorization: Bearer``; skips Auth0 entirely.
  * **Auth0 JWT** (default when no API key is set) — interactive humans via
    PKCE / Device Code / credential cache. Remains the default for laptops.

Token acquisition, in priority order:

  0. Workspace API key    — ctor ``api_key=`` or env ``VIOLET_API_KEY``.
                            Used verbatim; no cache, no refresh, no browser.
  1. Pre-supplied token   — env ``AUTH_TOKEN`` / ctor ``auth_token=``.
                            Used verbatim; no cache, no refresh.
  2. Cached access token  — from a previous login, if still valid.
  3. Refresh token        — silently mint a new access token (``offline_access``
                            scope yields a refresh token, so this is the steady
                            state after the first login).
  4. Interactive login    — Device Authorization Grant (default) or
                            Authorization Code + PKCE. Browser-based; the user
                            authenticates with Auth0 directly and nothing
                            secret is ever stored in or read from the SDK.

Auth0 application requirements (one-time, by whoever owns the tenant):
  * Device flow    → a Native app with the *Device Code* grant enabled.
  * PKCE loopback  → ``http://localhost:8765/callback`` (or your override) in
                     the app's *Allowed Callback URLs*.
  * Both           → the Messages API (``audience``) authorized for the app.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Optional

from .errors import AuthError

# ── Defaults: Violet production Auth0 tenant ──────────────────────
# Every value is overridable via constructor arg or env var. The defaults point
# at the same tenant the web app uses, so a token minted here is accepted by
# https://api.violetai.ca unchanged.
DEFAULT_DOMAIN = "dev-8vv5angslahu4n7h.ca.auth0.com"
DEFAULT_AUDIENCE = "https://api.prodkraft.com"
# The web SPA's public client id. SPA apps support Authorization Code + PKCE.
# For the Device flow you typically register a dedicated Native app and pass its
# id via AUTH0_CLIENT_ID / the constructor.
DEFAULT_CLIENT_ID = "Tk1qTB6fvVRuKZw5d0W6EWVK5Exm0CI1"
DEFAULT_SCOPE = "openid profile email offline_access"
# This exact loopback URL is registered in the Auth0 app's Allowed Callback URLs
# (the desktop app uses it — see violet-desktop/src/auth/auth.service.ts). PKCE
# against the SPA client only succeeds on a registered redirect, so default to it.
DEFAULT_REDIRECT = "http://localhost:5174/callback"

_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
_EXPIRY_SKEW = 60  # refresh a little early to avoid using a token mid-expiry


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class Authenticator:
    """Resolves and caches a Violet/Auth0 bearer token. Thread-safe."""

    def __init__(
        self,
        *,
        domain: Optional[str] = None,
        client_id: Optional[str] = None,
        audience: Optional[str] = None,
        scope: str = DEFAULT_SCOPE,
        redirect_uri: Optional[str] = None,
        cache_path: Optional[str] = None,
        # PKCE is the default: the prod SPA client supports Authorization Code +
        # PKCE (not Device Code). Use "device" only with a Native Auth0 app that
        # has the Device Code grant enabled.
        flow: str = "pkce",            # "pkce" | "device" | "none"
        open_browser: bool = True,
    ):
        self.domain = domain or os.environ.get("AUTH0_DOMAIN", DEFAULT_DOMAIN)
        self.client_id = client_id or os.environ.get("AUTH0_CLIENT_ID", DEFAULT_CLIENT_ID)
        self.audience = audience or os.environ.get("AUTH0_AUDIENCE", DEFAULT_AUDIENCE)
        self.scope = scope
        self.redirect_uri = redirect_uri or os.environ.get("VIOLET_REDIRECT_URI", DEFAULT_REDIRECT)
        self.flow = flow
        self.open_browser = open_browser

        default_cache = Path.home() / ".violet" / "credentials.json"
        self.cache_path = Path(cache_path or os.environ.get("VIOLET_CRED_CACHE", default_cache))

        # Priority: explicit api_key (set by Violet ctor) > VIOLET_API_KEY >
        # AUTH_TOKEN. API keys skip interactive Auth0 entirely.
        self._api_key = os.environ.get("VIOLET_API_KEY")
        self._pre = os.environ.get("AUTH_TOKEN")  # Auth0 JWT pre-supplied
        self._mem: Optional[dict] = None
        self._lock = threading.Lock()

    # ── Public API ─────────────────────────────────────────────────────────

    @property
    def uses_api_key(self) -> bool:
        """True when auth is a workspace ``vio_sk_…`` key (not Auth0)."""
        return bool(self._api_key and str(self._api_key).strip())

    def token(self) -> str:
        """Return a valid bearer (API key or Auth0 access token)."""
        if self._api_key:
            return self._api_key.strip()
        if self._pre:
            return self._pre.strip()

        with self._lock:
            tok = self._mem or self._load()
            if tok and not self._expired(tok):
                self._mem = tok
                return tok["access_token"]

            if tok and tok.get("refresh_token"):
                try:
                    tok = self._refresh(tok["refresh_token"], prev=tok)
                    self._store(tok)
                    return tok["access_token"]
                except AuthError:
                    pass  # refresh token revoked/expired → fall through to login

            if self.flow == "none":
                raise AuthError(
                    "No valid cached token and interactive login is disabled "
                    "(flow='none'). Set AUTH_TOKEN or call login()."
                )

            tok = self._interactive_login()
            self._store(tok)
            return tok["access_token"]

    def login(self) -> str:
        """Force an interactive login now (ignores cache). Returns the token."""
        with self._lock:
            tok = self._interactive_login()
            self._store(tok)
            return tok["access_token"]

    def logout(
        self,
        *,
        revoke: bool = True,
        browser: bool = False,
        return_to: Optional[str] = None,
    ) -> None:
        """Log the user out.

        revoke   – POST the cached refresh token to Auth0's ``/oauth/revoke``
                   so it can no longer mint access tokens (server-side). On by
                   default; best-effort (a failure is swallowed).
        browser  – open ``https://{domain}/v2/logout`` to clear the Auth0 SSO
                   session cookie, so the next interactive login re-prompts
                   instead of silently re-authenticating the same user.
        return_to – optional post-logout redirect; must be in the Auth0 app's
                   *Allowed Logout URLs* or Auth0 shows an error page. Omit to
                   land on Auth0's default logged-out page.

        Note: a pre-supplied ``AUTH_TOKEN`` (env) is not ours to revoke and
        still wins in :meth:`token` — unset the env var to truly sign out.
        """
        with self._lock:
            tok = self._mem or self._load()

            if revoke and tok and tok.get("refresh_token"):
                try:
                    # RFC 7009 revocation; public client → client_id, no secret.
                    self._post_form("/oauth/revoke", {
                        "client_id": self.client_id,
                        "token": tok["refresh_token"],
                    })
                except AuthError:
                    pass  # best-effort — still drop the local cache below

            self._mem = None
            cache = self._read_cache_file()
            cache.pop(self._cache_key(), None)
            self._write_cache_file(cache)

            if browser:
                params = {"client_id": self.client_id}
                if return_to:
                    params["returnTo"] = return_to
                url = f"https://{self.domain}/v2/logout?{urllib.parse.urlencode(params)}"
                opened = False
                if self.open_browser:
                    try:
                        opened = webbrowser.open(url)
                    except Exception:
                        opened = False
                if not opened:
                    print(f"  Open to end the Auth0 session:\n    {url}\n", file=sys.stderr)

        if self._pre:
            print(
                "  Note: AUTH_TOKEN is set in the environment and still takes "
                "precedence. Unset it to fully sign out:\n"
                "    Remove-Item Env:AUTH_TOKEN",
                file=sys.stderr,
            )

    # ── Token caching ──────────────────────────────────────────────────────

    def _cache_key(self) -> str:
        return f"{self.domain}|{self.client_id}|{self.audience}"

    def _read_cache_file(self) -> dict:
        try:
            return json.loads(self.cache_path.read_text("utf-8"))
        except (FileNotFoundError, ValueError):
            return {}

    def _write_cache_file(self, cache: dict) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(cache, indent=2), "utf-8")
        try:  # best-effort: keep credentials readable only by the owner
            os.chmod(self.cache_path, 0o600)
        except OSError:
            pass

    def _load(self) -> Optional[dict]:
        return self._read_cache_file().get(self._cache_key())

    def _store(self, tok: dict) -> None:
        self._mem = tok
        cache = self._read_cache_file()
        cache[self._cache_key()] = tok
        self._write_cache_file(cache)

    @staticmethod
    def _expired(tok: dict) -> bool:
        return time.time() >= (tok.get("expires_at", 0) - _EXPIRY_SKEW)

    def _materialize(self, resp: dict, prev: Optional[dict] = None) -> dict:
        """Turn an Auth0 token response into our cached shape."""
        tok = {
            "access_token": resp["access_token"],
            "token_type": resp.get("token_type", "Bearer"),
            "scope": resp.get("scope", self.scope),
            "expires_at": time.time() + int(resp.get("expires_in", 3600)),
            # Refresh-token rotation may omit a new RT; keep the previous one.
            "refresh_token": resp.get("refresh_token") or (prev or {}).get("refresh_token"),
        }
        return tok

    # ── Auth0 HTTP ─────────────────────────────────────────────────────────

    def _post_form(self, path: str, form: dict) -> tuple[int, Any]:
        url = f"https://{self.domain}{path}"
        data = urllib.parse.urlencode(form).encode()
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read() or b"{}")
            except ValueError:
                body = {"error": f"http_{e.code}"}
            return e.code, body
        except urllib.error.URLError as e:
            raise AuthError(f"could not reach Auth0 ({self.domain}): {e}") from e

    def _refresh(self, refresh_token: str, prev: dict) -> dict:
        status, resp = self._post_form("/oauth/token", {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "refresh_token": refresh_token,
        })
        if status != 200 or "access_token" not in resp:
            raise AuthError(f"token refresh failed: {resp}")
        return self._materialize(resp, prev=prev)

    # ── Interactive flows ──────────────────────────────────────────────────

    def _interactive_login(self) -> dict:
        if self.flow == "pkce":
            return self._pkce_login()
        return self._device_login()

    def _device_login(self) -> dict:
        status, dev = self._post_form("/oauth/device/code", {
            "client_id": self.client_id,
            "scope": self.scope,
            "audience": self.audience,
        })
        if status != 200 or "device_code" not in dev:
            raise AuthError(
                f"device authorization request failed: {dev}. The Auth0 app "
                f"'{self.client_id}' likely needs the Device Code grant enabled."
            )

        verify = dev.get("verification_uri_complete") or dev.get("verification_uri")
        user_code = dev.get("user_code", "")
        print(
            "\n  To sign in, open this URL in your browser:\n"
            f"    {verify}\n"
            f"  and confirm the code:  {user_code}\n",
            file=sys.stderr,
        )
        if self.open_browser and verify:
            try:
                webbrowser.open(verify)
            except Exception:
                pass

        interval = int(dev.get("interval", 5))
        deadline = time.time() + int(dev.get("expires_in", 600))
        device_code = dev["device_code"]

        while time.time() < deadline:
            time.sleep(interval)
            status, resp = self._post_form("/oauth/token", {
                "grant_type": _DEVICE_GRANT,
                "device_code": device_code,
                "client_id": self.client_id,
            })
            if status == 200 and "access_token" in resp:
                print("  Signed in.\n", file=sys.stderr)
                return self._materialize(resp)
            err = resp.get("error")
            if err == "authorization_pending":
                continue
            if err == "slow_down":
                interval += 5
                continue
            if err in ("expired_token", "access_denied"):
                raise AuthError(f"device login failed: {err}")
            raise AuthError(f"unexpected device-token response: {resp}")

        raise AuthError("device login timed out before the code was confirmed")

    def _pkce_login(self) -> dict:
        verifier = _b64url(secrets.token_bytes(32))
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        state = _b64url(secrets.token_bytes(16))
        nonce = _b64url(secrets.token_bytes(16))

        parsed = urllib.parse.urlparse(self.redirect_uri)
        host = parsed.hostname or "localhost"
        port = parsed.port or 80
        cb_path = parsed.path or "/callback"

        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": self.scope,
            "audience": self.audience,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "nonce": nonce,
        }
        authorize_url = f"https://{self.domain}/authorize?{urllib.parse.urlencode(params)}"

        captured: dict[str, str] = {}

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                q = urllib.parse.urlparse(self.path)
                if q.path != cb_path:
                    self.send_response(404)
                    self.end_headers()
                    return
                captured.update(dict(urllib.parse.parse_qsl(q.query)))
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"<html><body><h2>Violet sign-in complete.</h2>"
                    b"You can close this tab and return to your terminal.</body></html>"
                )

            def log_message(self, *_):  # silence default stderr logging
                pass

        server = HTTPServer((host, port), _Handler)
        print(
            f"\n  Opening browser to sign in. If it doesn't open, visit:\n    {authorize_url}\n",
            file=sys.stderr,
        )
        if self.open_browser:
            try:
                webbrowser.open(authorize_url)
            except Exception:
                pass

        server.timeout = 300
        try:
            while "code" not in captured and "error" not in captured:
                server.handle_request()
        finally:
            server.server_close()

        if captured.get("error"):
            raise AuthError(f"login failed: {captured.get('error_description') or captured['error']}")
        if captured.get("state") != state:
            raise AuthError("login failed: state mismatch (possible CSRF)")

        status, resp = self._post_form("/oauth/token", {
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "code": captured["code"],
            "code_verifier": verifier,
            "redirect_uri": self.redirect_uri,
        })
        if status != 200 or "access_token" not in resp:
            raise AuthError(f"code exchange failed: {resp}")
        print("  Signed in.\n", file=sys.stderr)
        return self._materialize(resp)
