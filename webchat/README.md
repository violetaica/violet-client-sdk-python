# `webchat` — Violet SDK full-stack showcase

A minimal but complete web chat: a **FastAPI + Server-Sent-Events** backend that
proxies the Violet SDK's streaming to a single-page browser UI.

The interesting bit is the trust boundary. The browser never sees a credential —
it only ever exchanges chat text with *this* backend. The backend constructs one
module-level `Violet()` client, which resolves the Auth0 token **server-side**,
and relays `client.messages.stream(...)` to the page over SSE.

```
browser  ──fetch/EventSource──▶  FastAPI (holds the token)  ──Violet SDK──▶  gateway
   ▲                                   │
   └────────  data: <chunk>  ◀─────────┘   (SSE: chunks, then data: [DONE])
```

## What it shows

- Streaming an LLM reply token-by-token to a browser over `text/event-stream`.
- Running the SDK's **blocking** stream in a worker thread
  (`starlette ... iterate_in_threadpool`) so it never stalls the asyncio loop.
- Keeping the auth token entirely server-side.
- The Violet extension `client.usage.get()` exposed as a plain JSON endpoint.

## Files

| Path                 | Role                                                        |
| -------------------- | ----------------------------------------------------------- |
| `app.py`             | FastAPI app: `/`, `/api/chat` (SSE), `/api/usage` |
| `static/index.html`  | Self-contained page (inline CSS/JS, no build step, no CDN)  |

## Install

from the repo root (the `web` extra pulls in `fastapi` + `uvicorn`):

```powershell
pip install -e ".[web]"
```

## Run

```powershell
python -m webchat.app
# or, with autoreload during development:
uvicorn webchat.app:app --reload
```

Then open <http://127.0.0.1:8000>. Override host/port/model with the env vars
`WEBCHAT_HOST`, `WEBCHAT_PORT`, `WEBCHAT_MAX_TOKENS`, `MESSAGES_TEST_MODEL`.

## Endpoints

| Method | Path           | Returns                                                       |
| ------ | -------------- | ------------------------------------------------------------ |
| GET    | `/`            | the chat UI (`static/index.html`)                            |
| POST   | `/api/chat`    | SSE stream; body `{"messages": [{role, content}, ...]}`      |
| GET    | `/api/chat`    | SSE stream; `?message=…&history=<json>` (for `EventSource`)  |
| GET    | `/api/usage`   | monthly USD billing snapshot                                 |

SSE frames are `data: <chunk>\n\n`, a `data: [ERROR] <message>\n\n` on a
`VioletError`, and a final `data: [DONE]\n\n`.

## Prereqs

> - A paid, provisioned workspace (production gateway by default).
> - A **one-time browser login** (Auth0 PKCE) on the first request; the token is
>   cached after that.
> - Samples pass a placeholder `model=` (`Violet Test Model`); the gateway
>   sets the served model — the client cannot choose it.

## Security note

The Auth0 token lives only in the backend process (the module-level `Violet()`
client) and is never sent to or exposed in the browser.
