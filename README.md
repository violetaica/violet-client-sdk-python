# Violet Messages API — Python Client SDK

A small, dependency-free Python client for the **Violet Messages API** —
`client.messages.create(...)`, `msg.content[0].text`, `client.messages.stream(...)`,
and typed error classes. *(Familiar if you know the Anthropic Messages SDK surface;
this package is Violet-native and talks to the Violet gateway.)*

```python
from violet import Violet

client = Violet(workspace_id="ws_…")            # Auth0 browser login on first call
# CI / server — workspace API key (Settings → API keys):
# client = Violet(api_key="vio_sk_…")           # or VIOLET_API_KEY
# model= is required by the API shape; the gateway chooses the served model.
msg = client.messages.create(model="Violet Test Model", max_tokens=64,
                             messages=[{"role": "user", "content": "Say hi"}])
print(msg.content[0].text)
```

Authentication is **dual**:

- **Auth0 JWT** (default when no API key is set) — interactive humans via PKCE /
  Device Code / credential cache. Same as the web and desktop apps.
- **Workspace API key** (`api_key=` / `VIOLET_API_KEY`) — opaque `vio_sk_…`
  secret for CI, servers, and partner backends. Minted in Settings → API keys.

## Auth priority

| Order | Mode | When to use |
|---|---|---|
| 0 | `api_key=` / `VIOLET_API_KEY` | CI, servers, headless jobs |
| 1 | `AUTH_TOKEN` / `auth_token=` (pre-supplied Auth0 JWT) | Devtools / scripts |
| 2 | Cached access token from a prior login | Steady state after browser login |
| 3 | Refresh token (`offline_access`) → silent re-mint | Steady state |
| 4 | **Interactive login**: Authorization Code + PKCE (default) or Device Code | Laptops / demos |

Auth0 refresh + access tokens are cached at `~/.violet/credentials.json`
(mode `600`), keyed by `(domain, client_id, audience)`. API keys are **not**
cached by the SDK — store them in your secret manager.

## Install

```powershell
pip install -e .          # or: pip install -e ".[dev]"
```

Requires Python 3.9+. No third-party packages.

## Surface

| Call | Endpoint |
|---|---|
| `Violet(...)` / `AsyncViolet(...)` | — |
| `client.messages.create(...) -> Message` | `POST /api/v1/messages` |
| `client.messages.stream(...)` → `.text_stream`, `.get_final_message()` | `POST /api/v1/messages` (SSE) |
| `client.messages.create(..., stream=True)` → event iterator | SSE |
| `client.messages.count_tokens(...) -> .input_tokens` | `POST /api/v1/messages/count_tokens` |
| `client.messages.parse(..., output_format=…) -> .parsed_output` | structured output |
| `Message`, `TextBlock`, `ThinkingBlock`, `ToolUseBlock`, `Usage` | — |
| `BadRequestError` / `AuthenticationError` / `PermissionDeniedError` / `NotFoundError` / `RateLimitError` / `InternalServerError` → `APIError` | — |
| `client.with_options(timeout=, max_retries=)` | — |

**Violet extensions:**
`client.auth.login()/logout()/signup()/profile()`, `client.usage.get()` (monthly USD spend),
`client.health()`. Errors also carry `.code` (e.g. `user_not_provisioned`).

Auth is Auth0 login **or** a workspace `api_key=` (`vio_sk_…`, not a provider key)
plus `workspace_id`. The **gateway sets the served model** — client `model=` is
required by the API shape but does not choose inference.

## Quick start

```python
from violet import Violet

client = Violet()                # auth + base URL + workspace from env / defaults

msg = client.messages.create(    # first call triggers a one-time browser sign-in
    model="Violet Test Model",   # placeholder — gateway sets the real model
    max_tokens=64,
    messages=[{"role": "user", "content": "Say hello in 3 words."}],
)
print(msg.content[0].text)
print(msg.usage.input_tokens, msg.usage.output_tokens, msg.stop_reason)
```

Streaming:

```python
with client.messages.stream(
    model="Violet Test Model", max_tokens=128,
    messages=[{"role": "user", "content": "Write a haiku about violets."}],
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)
    final = stream.get_final_message()
```

Errors:

```python
from violet import RateLimitError, PermissionDeniedError, APIError
try:
    client.messages.create(...)
except RateLimitError as e:
    ...                      # 429
except PermissionDeniedError as e:
    print(e.code)            # e.g. "user_not_provisioned"
except APIError as e:
    print(e.status_code, e.message)
```

### Async

`AsyncViolet` is a **native-async** client — non-blocking I/O via **aiohttp**,
an optional extra: `pip install violet-sdk[async]`. The sync `Violet` stays
stdlib-only; importing `violet` never
requires aiohttp. Same surface as `Violet`, just `await`-ed:

```python
import asyncio
from violet import AsyncViolet

async def main():
    async with AsyncViolet(workspace_id="ws_…") as client:
        msg = await client.messages.create(
            model="Violet Test Model", max_tokens=64,  # gateway sets the model
            messages=[{"role": "user", "content": "Say hi"}],
        )
        print(msg.content[0].text)

        async with client.messages.stream(
            model="Violet Test Model", max_tokens=128,
            messages=[{"role": "user", "content": "Write a haiku."}],
        ) as stream:
            async for text in stream.text_stream:
                print(text, end="", flush=True)
            final = await stream.get_final_message()

asyncio.run(main())
```

Use `async with AsyncViolet(...)` (or call `await client.aclose()`) so the
underlying HTTP session is closed. The rare Auth0 token resolution runs in a
thread; the message/stream calls are genuinely async.

Run the bundled examples:

```powershell
python -m examples.chat
python -m examples.stream
python -m examples.whoami     # diagnose auth/provisioning
```

**Sample apps** — per-method `examples/`, technique `cookbook/` recipes, and a
full-stack `webchat/` showcase are cataloged in **[SAMPLES.md](SAMPLES.md)**
(with the shared prereqs and the backend-reality caveats).

## Configuration

All via constructor args or env vars (env shown):

| Var | Default | Purpose |
|---|---|---|
| `MESSAGES_API_URL` | `https://api.violetai.ca` | Gateway base URL |
| `WORKSPACE_ID` | Auth0: auto via `/api/auth/login`; API key: omit (gateway injects) | Tenant scope (`X-Workspace-Id`) |
| `AUTH0_DOMAIN` | `dev-8vv5angslahu4n7h.ca.auth0.com` | Auth0 tenant |
| `AUTH0_CLIENT_ID` | web SPA client id | Public OAuth client (no secret) |
| `AUTH0_AUDIENCE` | `https://api.prodkraft.com` | Token audience the gateway validates |
| `AUTH_TOKEN` | *(unset)* | Skip login; use this access token verbatim |
| `VIOLET_REDIRECT_URI` | `http://localhost:5174/callback` | PKCE loopback redirect (registered in Auth0) |
| `VIOLET_CRED_CACHE` | `~/.violet/credentials.json` | Token cache location |
| `VIOLET_USER_AGENT` | browser-like UA | Sent on gateway calls so Cloudflare doesn't 403 the client as a bot |

Pick the interactive flow explicitly:

```python
from violet import Authenticator, Violet

client = Violet(auth=Authenticator(flow="pkce"))   # default; or flow="device"
client.auth.force_login()   # force interactive sign-in now
client.auth.logout()        # clear cached credentials
```

## Logging out

```python
client.auth.logout()                 # revoke refresh token at Auth0 + delete local cache
client.auth.logout(browser=True)     # also end the Auth0 SSO session (next login re-prompts)
client.auth.logout(revoke=False)     # local-only: just forget the cached tokens
```

Or from the CLI:

```powershell
python -m examples.logout            # revoke + clear local cache
python -m examples.logout --browser  # also clear the Auth0 session cookie
```

Three layers, pick what you need:

| Goal | Call |
|---|---|
| Forget tokens on this machine | `client.auth.logout(revoke=False)` |
| …and invalidate the refresh token server-side | `client.auth.logout()` *(default)* |
| …and force the next login to re-prompt (switch accounts) | `client.auth.logout(browser=True)` |

The `browser=True` step matters because Auth0 keeps an SSO session cookie — without
clearing it, the next PKCE login silently re-authenticates the *same* user and
"logout" looks like it did nothing. (A `return_to` URL passed here must be in the
app's **Allowed Logout URLs**, or Auth0 shows an error page.)

> **`AUTH_TOKEN` caveat:** if you signed in via the pre-supplied-token path, `logout`
> can't revoke that token (it isn't ours) and the env var still wins on the next call.
> Unset it to fully sign out: `Remove-Item Env:AUTH_TOKEN`.

## Auth0 application prerequisites (one-time, tenant owner)

The defaults point at Violet's production tenant and the **same public SPA client
the web/desktop apps use** (`Tk1qTB6fvVRuKZw5d0W6EWVK5Exm0CI1`), which supports
Authorization Code + PKCE.

- **PKCE loopback (default)** → works out of the box: the SDK redirects to
  `http://localhost:5174/callback`, which is registered in the Auth0 SPA app.
  Packaged Violet Electron uses `http://127.0.0.1:5174/callback` (same port,
  different host) — that URL must also be in **Allowed Callback URLs**,
  **Allowed Logout URLs**, and **Allowed Web Origins**. If you change
  `VIOLET_REDIRECT_URI`, add that exact URL or login fails with *Callback URL
  mismatch*. Port `5174` must be free during login (stop the desktop
  dev/packaged loopback server if it's running).
- **Device Code** flow → **not enabled on the SPA client** (you'll get
  `unauthorized_client: Grant type 'device_code' not allowed`). To use it,
  register a **Native** Auth0 app with the *Device Code* grant enabled and pass
  its id via `AUTH0_CLIENT_ID`.
- Either flow → the Messages API (`AUTH0_AUDIENCE`) must be authorized for the app.

To skip interactive login entirely, set `AUTH_TOKEN` with a token grabbed from the
web app's dev tools (DevTools → Network → any `/api/v1/*` request → the
`Authorization: Bearer …` header).

## Cloudflare `403 error code: 1010`

The prod gateway (`api.violetai.ca`) is behind **Cloudflare bot protection**, which
bans the default `Python-urllib/x` user-agent. The SDK sends a browser-like
`User-Agent` by default to avoid it. If you still see `1010`:

- Override the UA: `Violet(user_agent="…")` or `$env:VIOLET_USER_AGENT = "…"`.
- Cloudflare may also fingerprint at the TLS layer (JA3) — if a UA alone doesn't get
  through, you're hitting stricter bot management; ask the API team to allowlist
  your client.

## Provisioning & 403s

**Login succeeding but inference returning `403` is normal for a brand-new account.**
Auth0 only proves *who you are*; the Violet gateway separately checks that the
identity is a **provisioned account** with a **paid workspace**. Two distinct gates:

```powershell
python -m examples.whoami   # tells you exactly which gate you're failing
```

| Symptom | Meaning | Fix |
|---|---|---|
| `403 user_not_provisioned` | Valid Auth0 token, but no Violet account row | `client.auth.signup()` to provision the account row |
| Signed in, `default_workspace` is null | Provisioned, but no workspace | Subscribe/create a workspace in the **web app** (signup alone doesn't — billing does), then retry |
| `403 Not a member of this workspace` | The `WORKSPACE_ID` you sent isn't one you belong to | Use a workspace you're a member of (leave `WORKSPACE_ID` unset to auto-resolve your default) |
| `403 subscription_required` | `POST /api/workspaces` without a paid plan | Complete checkout in the web app |

```python
client = Violet()
client.auth.signup()             # provision the account row (idempotent)
print(client.auth.login())       # inspect sub / default_workspace / memberships_count
# If you already know your workspace id, skip auto-resolution:
client = Violet(workspace_id="ws-...")   # or set WORKSPACE_ID env
```

With Auth0, the SDK auto-resolves your workspace from `/api/auth/login` on the
first member call. With a workspace API key (`vio_sk_…`), the header is omitted
and the gateway injects the key's bound workspace — so narrow scopes
(`messages:write`, `usage:read`) work without a full-access key. If Auth0
login finds no provisioned account or workspace, the SDK raises a **clear**
`VioletError` instead of a bare `403`.

## Endpoints behind the surface

| Surface call | Endpoint |
|---|---|
| `client.messages.create/stream` | `POST /api/v1/messages` |
| `client.messages.count_tokens` | `POST /api/v1/messages/count_tokens` |
| `client.usage.get` | `GET /api/v1/usage/me` (monthly billing snapshot) |
| `client.auth.login/signup/profile` | `/api/auth/login`, `/api/auth/signup`, `/api/auth/profile` |
| `client.health()` | `GET /api/health` (+ fallbacks) |

Errors use the typed classes (`BadRequestError` … `RateLimitError`,
all `APIError`/`APIStatusError`) plus `.code`; auth-flow failures raise
`AuthError`. All subclass `VioletError`.
