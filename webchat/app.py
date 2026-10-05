"""Violet web-chat — a FastAPI + Server-Sent-Events showcase.

A tiny full-stack demo where the browser talks to *this* backend, and the
backend holds the Auth0 token SERVER-SIDE and proxies the Violet SDK's
streaming to the page over SSE. The token never reaches the browser.

Endpoints
    GET  /             -> the single-page chat UI (static/index.html)
    POST /api/chat     -> SSE stream; JSON body {"messages": [{role, content}, ...]}
                          (or {"message": ..., "history": [...]})
    GET  /api/chat     -> same, for EventSource clients (?message=&history=)
    GET  /api/usage    -> workspace monthly USD spend

SSE wire format (consumed by static/index.html):
    data: <chunk of assistant text>\n\n   # zero or more, streamed live
    data: [ERROR] <message>\n\n            # on a VioletError
    data: [DONE]\n\n                        # always, last frame

Run
    pip install -e ".[web]"        # one-time; pulls fastapi + uvicorn
    python -m webchat.app          # or: uvicorn webchat.app:app --reload

Prereqs: a paid workspace (Auth0 login or `VIOLET_API_KEY`) and a
one-time browser login on first request. ``model=`` is a placeholder — the
gateway sets the served model (default env MESSAGES_TEST_MODEL /
"Violet Test Model").

This is an ORIGINAL implementation against the Violet SDK — not copied from
third-party sample repos.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterator, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from starlette.concurrency import iterate_in_threadpool

from violet import Violet, VioletError

# Placeholder — API requires model=; gateway sets the served model (client cannot).
MODEL = os.environ.get("MESSAGES_TEST_MODEL", "Violet Test Model")
MAX_TOKENS = int(os.environ.get("WEBCHAT_MAX_TOKENS", "1024"))
STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Violet web-chat")

# One server-side client for the whole process. The Auth0 token it resolves
# stays here — browsers only ever see model text, never the credential.
client = Violet()


def _sse(data: str) -> str:
    """Frame a single payload as one Server-Sent-Events `data:` record."""
    return f"data: {data}\n\n"


def _normalize(payload: dict) -> List[dict]:
    """Accept either {"messages": [...]} or {"message": str, "history": [...]}.

    Returns a clean `messages` array of {"role", "content"} dicts with the new
    user turn last.
    """
    raw = payload.get("messages")
    if not isinstance(raw, list):
        raw = list(payload.get("history") or [])
        msg = payload.get("message")
        if isinstance(msg, str) and msg.strip():
            raw = raw + [{"role": "user", "content": msg}]
    out: List[dict] = []
    for turn in raw:
        if not isinstance(turn, dict):
            continue
        role, content = turn.get("role"), turn.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content:
            out.append({"role": role, "content": content})
    return out


def _token_stream(messages: List[dict]) -> Iterator[str]:
    """Yield SSE frames from the SDK's blocking stream.

    Runs in a worker thread (see iterate_in_threadpool below) so the blocking
    HTTP reads inside the SDK never stall the asyncio event loop. Each text
    delta becomes one `data:` frame; a trailing `[DONE]` always marks the end.
    A VioletError is surfaced as a friendly `[ERROR]` frame, not a dropped
    socket or a 500.
    """
    try:
        with client.messages.stream(
            model=MODEL, max_tokens=MAX_TOKENS, messages=messages,
        ) as stream:
            for chunk in stream.text_stream:
                if chunk:
                    yield _sse(chunk)
    except VioletError as e:
        yield _sse(f"[ERROR] {e}")
    except Exception as e:  # pragma: no cover - defensive; keep the socket clean
        yield _sse(f"[ERROR] unexpected error: {e}")
    finally:
        yield _sse("[DONE]")


def _stream_response(messages: List[dict]) -> Any:
    if not messages or messages[-1].get("role") != "user":
        return JSONResponse({"error": "a trailing user message is required"}, status_code=400)
    body = iterate_in_threadpool(_token_stream(messages))
    return StreamingResponse(
        body,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/", response_class=HTMLResponse)
def index() -> Any:
    """Serve the single-page chat UI."""
    page = STATIC / "index.html"
    if page.exists():
        return FileResponse(page)
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><title>Violet web-chat</title>"
        "<p>UI file <code>static/index.html</code> is missing. The API still "
        "works: try POST <code>/api/chat</code>.</p>"
    )


@app.post("/api/chat")
async def chat_post(request: Request) -> Any:
    """SSE chat over a JSON body: {"messages": [...]} (the page's path)."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    return _stream_response(_normalize(payload or {}))


@app.get("/api/chat")
def chat_get(message: str = "", history: str = "") -> Any:
    """SSE chat over query params, for raw `EventSource` (GET-only) clients."""
    try:
        hist = json.loads(history) if history else []
    except json.JSONDecodeError:
        hist = []
    return _stream_response(_normalize({"message": message, "history": hist}))


@app.get("/api/usage")
def usage() -> Any:
    """Workspace monthly billing snapshot (USD spend) — a Violet extension."""
    try:
        rpt = client.usage.get()
        return {
            "usage_monthly_usd": rpt.usage_monthly_usd,
            "limit_monthly_usd": rpt.limit_monthly_usd,
            "limit_remaining_usd": rpt.limit_remaining_usd,
            "resets_at": rpt.resets_at,
        }
    except VioletError as e:
        return JSONResponse({"error": str(e)}, status_code=502)


def main() -> None:
    import uvicorn

    host = os.environ.get("WEBCHAT_HOST", "127.0.0.1")
    port = int(os.environ.get("WEBCHAT_PORT", "8000"))
    print(f"Violet web-chat on http://{host}:{port}  (model={MODEL})")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
