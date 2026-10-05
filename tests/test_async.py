"""Async tests for AsyncViolet — no external network.

1. Parser-level: drive AsyncMessageStream from a fake chunked async byte source
   (no aiohttp) and check text_stream + get_final_message + tool-use reassembly.
2. End-to-end: stand up a local aiohttp server and exercise the REAL async
   transport (create / stream / count_tokens / usage), asserting the server saw
   the right auth/workspace/UA headers.

Run: PYTHONPATH=. python tests/test_async.py
"""
from __future__ import annotations

import asyncio

from violet import AsyncViolet, fixtures
from violet._async import AsyncMessageStream


def assert_(cond, msg):
    if not cond:
        raise AssertionError(msg)


# ── 1. Parser-level (no aiohttp) ──────────────────────────────────────────────

async def _fake_bytes(data, n: int = 7):
    if isinstance(data, str):
        data = data.encode()
    for i in range(0, len(data), n):
        yield data[i : i + n]


async def test_async_stream_parser():
    sse = fixtures.sse_message(text="hi there")

    async def opener():
        return _fake_bytes(sse), None

    s = AsyncMessageStream(opener)
    chunks = []
    async for t in s.text_stream:
        chunks.append(t)
    assert_("".join(chunks) == "hi there", f"text_stream concat: {chunks}")
    fm = await s.get_final_message()
    assert_(fm.content[0].text == "hi there", "final text")
    assert_(fm.stop_reason == "end_turn" and fm.usage.output_tokens > 0, "final stop/usage")

    sse2 = fixtures.sse_message(tool_use={"name": "calc", "input": {"operation": "multiply", "a": 17, "b": 23}})

    async def opener2():
        return _fake_bytes(sse2, 5), None

    s2 = AsyncMessageStream(opener2)
    async for _ in s2:
        pass
    tu = (await s2.get_final_message()).content[0]
    assert_(tu.type == "tool_use" and tu.input["a"] == 17 and tu.input["b"] == 23, "tool input reassembly")


# ── 2. End-to-end against a local aiohttp server ──────────────────────────────

async def test_async_end_to_end():
    try:
        from aiohttp import web
    except ModuleNotFoundError:
        print("SKIP end-to-end: aiohttp not installed")
        return

    seen = {}

    async def messages(request):
        seen["headers"] = dict(request.headers)
        body = await request.json()
        if body.get("stream"):
            resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await resp.prepare(request)
            await resp.write(fixtures.sse_message(text="hi there"))
            await resp.write_eof()
            return resp
        return web.json_response(fixtures.message(text="pong", input_tokens=3, output_tokens=2))

    async def count_tokens(request):
        return web.json_response({"input_tokens": 42})

    async def usage_me(request):
        return web.json_response(
            {"workspace_id": "ws-1", "usage_monthly_usd": 1.23, "limit_monthly_usd": 10.0,
             "limit_remaining_usd": 8.77, "resets_at": "2026-07-01T00:00:00Z"}
        )

    app = web.Application()
    app.router.add_post("/api/v1/messages", messages)
    app.router.add_post("/api/v1/messages/count_tokens", count_tokens)
    app.router.add_get("/api/v1/usage/me", usage_me)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]

    try:
        async with AsyncViolet(
            base_url=f"http://127.0.0.1:{port}", auth_token="tok-x",
            workspace_id="ws-1", user_agent="violet-test/1", timeout=10.0,
        ) as client:
            # non-streaming create
            msg = await client.messages.create(
                model="Violet Test Model", max_tokens=8,
                messages=[{"role": "user", "content": "ping"}],
            )
            assert_(msg.content[0].text == "pong", "create text")
            assert_(msg.usage.input_tokens == 3, "create usage")

            # the server saw our auth/workspace/UA headers
            h = seen["headers"]
            assert_(h.get("Authorization") == "Bearer tok-x", "Authorization header")
            assert_(h.get("X-Workspace-Id") == "ws-1", "X-Workspace-Id header")
            assert_(h.get("User-Agent") == "violet-test/1", "User-Agent header")

            # streaming
            collected = []
            async with client.messages.stream(
                model="Violet Test Model", max_tokens=8,
                messages=[{"role": "user", "content": "go"}],
            ) as stream:
                async for t in stream.text_stream:
                    collected.append(t)
                final = await stream.get_final_message()
            assert_("".join(collected) == "hi there", f"stream text: {collected}")
            assert_(final.stop_reason == "end_turn" and final.usage.output_tokens > 0, "stream final")

            # count_tokens + usage
            tc = await client.messages.count_tokens(
                model="Violet Test Model", messages=[{"role": "user", "content": "hello"}]
            )
            assert_(tc.input_tokens == 42, "count_tokens")
            rpt = await client.usage.get()
            assert_(rpt.usage_monthly_usd == 1.23 and rpt.limit_remaining_usd == 8.77, "usage shape")
    finally:
        await runner.cleanup()


async def main():
    await test_async_stream_parser()
    print("PASS test_async_stream_parser")
    await test_async_end_to_end()
    print("PASS test_async_end_to_end")
    print("\nASYNC_ALL_OK")


if __name__ == "__main__":
    asyncio.run(main())
