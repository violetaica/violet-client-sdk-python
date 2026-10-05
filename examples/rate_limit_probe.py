"""Prod probe for workspace RPM / ITPM / OTPM (+ optional auth IP RPM).

Ceilings are seeded per plan tier (see harness ``_PLAN_RATE_CAPS``):

  Free   rpm=30   itpm=50_000   otpm=10_000
  Core   rpm=60   itpm=100_000  otpm=20_000
  Pro    rpm=120  itpm=250_000  otpm=50_000
  Teams  rpm=180  itpm=400_000  otpm=80_000

Admission runs on ``POST /api/v1/messages`` *before* the LLM call. A 429 with
``code=rate_limit_exceeded`` and ``metric=rpm`` means the RPM limiter is live.

``rpm`` mode fires a **concurrent burst** (many admits race in the same minute).
A slow serial loop cannot hit Core/Pro ceilings — each LLM call takes seconds.

Fairness (``max_concurrent_turns``) is a *different* 429 — the burst reports both.

Auth IP gate (separate): ``/api/auth/*`` is 20 req/min per IP by default
(``AUTH_IP_RPM``).

    pip install -e .
    $env:VIOLET_API_KEY = "vio_sk_…"
    python -m examples.rate_limit_probe rpm --tier Core
    python -m examples.rate_limit_probe otpm --tier Core   # single-request smoke
    python -m examples.rate_limit_probe itpm --tier Free
    python -m examples.rate_limit_probe auth
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from violet import RateLimitError, Violet, VioletError

DEFAULT_BASE = "https://api.violetai.ca"
# Placeholder — API requires model=; gateway sets the served model (client cannot).
DEFAULT_MODEL = os.environ.get("MESSAGES_TEST_MODEL", "Violet Test Model")

TIER_RPM = {"Free": 30, "Core": 60, "Pro": 120, "Teams": 180}
TIER_ITPM = {"Free": 50_000, "Core": 100_000, "Pro": 250_000, "Teams": 400_000}
TIER_OTPM = {"Free": 10_000, "Core": 20_000, "Pro": 50_000, "Teams": 80_000}


def _client() -> Violet:
    if not (os.environ.get("VIOLET_API_KEY") or os.environ.get("api_key")):
        print("Set VIOLET_API_KEY (workspace key from Settings → API keys).", file=sys.stderr)
        sys.exit(2)
    return Violet(max_retries=0)


def _rl_body(err: RateLimitError) -> dict:
    return err.body if isinstance(err.body, dict) else {}


def _auth_headers(client: Violet) -> dict[str, str]:
    """Resolve Authorization (+ workspace) the same way the SDK transport would."""
    ws = client._ensure_workspace()  # noqa: SLF001 — probe needs the resolved tenant
    return client._transport.headers(  # noqa: SLF001
        workspace_id=ws, json_body=True, stream=False
    )


def _post_messages(url: str, headers: dict[str, str], i: int, timeout: float) -> dict[str, Any]:
    body = {
        "model": DEFAULT_MODEL,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": f"Reply with OK only. burst#{i}"}],
    }
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read() or b""
            try:
                payload = json.loads(raw) if raw else {}
            except ValueError:
                payload = {}
            return {
                "i": i,
                "status": resp.status,
                "dt": time.perf_counter() - t0,
                "body": payload,
                "headers": {k.lower(): v for k, v in resp.headers.items()},
            }
    except urllib.error.HTTPError as e:
        raw = e.read() or b""
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:
            payload = {}
        return {
            "i": i,
            "status": e.code,
            "dt": time.perf_counter() - t0,
            "body": payload,
            "headers": {k.lower(): v for k, v in e.headers.items()} if e.headers else {},
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "i": i,
            "status": 0,
            "dt": time.perf_counter() - t0,
            "body": {"error": str(exc)},
            "headers": {},
        }


def probe_rpm(expect: int, *, workers: int, extra: int, timeout: float) -> int:
    """Burst many concurrent POSTs so admits race inside one minute window."""
    client = _client()
    n = expect + extra
    workers = max(workers, n)
    url = f"{client.base_url.rstrip('/')}/api/v1/messages"
    headers = _auth_headers(client)

    print(
        f"RPM burst: {n} concurrent POSTs (workers={workers}, expect ceiling ≈ {expect})"
    )
    print(f"gateway={client.base_url}  timeout={timeout}s\n")
    print(
        "Note: fairness may also 429 some requests after admit; look for "
        "code=rate_limit_exceeded metric=rpm.\n"
    )

    t0 = time.perf_counter()
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [
            pool.submit(_post_messages, url, headers, i, timeout) for i in range(1, n + 1)
        ]
        for fut in as_completed(futs):
            results.append(fut.result())
    elapsed = time.perf_counter() - t0
    results.sort(key=lambda r: r["i"])

    ok = 0
    rpm_429 = 0
    fair_429 = 0
    other_429 = 0
    err = 0
    rpm_limits: list[int] = []

    for r in results:
        status = r["status"]
        body = r["body"] if isinstance(r["body"], dict) else {}
        code = body.get("code")
        metric = body.get("metric")
        # Fairness / turn_pool shapes vary; treat non-rate_limit 429 as fairness-ish.
        if status == 200:
            ok += 1
            label = "200"
        elif status == 429 and code == "rate_limit_exceeded" and metric == "rpm":
            rpm_429 += 1
            if isinstance(body.get("limit"), int):
                rpm_limits.append(body["limit"])
            label = (
                f"429 RPM  used={body.get('used')}/{body.get('limit')} "
                f"retry_after={body.get('retry_after_s')}"
            )
        elif status == 429:
            if code in {"rate_limit_exceeded", "rate_limit_unavailable"}:
                other_429 += 1
                label = f"429 rate_limit metric={metric} code={code}"
            else:
                fair_429 += 1
                label = f"429 fairness/other code={code or body.get('type')}"
        else:
            err += 1
            label = f"{status} {body.get('error') or body.get('message') or ''}"
        print(f"  [{r['i']:3d}] {r['dt']:5.2f}s  {label}")

    print(
        f"\nSummary in {elapsed:.1f}s: 200={ok}  rpm_429={rpm_429}  "
        f"fairness/other_429={fair_429}  other_rl_429={other_429}  err={err}"
    )
    if rpm_limits:
        print(f"Reported RPM limit from 429 body: {rpm_limits[0]}")

    if rpm_429 > 0:
        lim = rpm_limits[0] if rpm_limits else None
        print("\nOK: workspace RPM limiter is live in prod.")
        if lim is not None and expect and lim != expect:
            print(f"NOTE: body.limit={lim} but --expect/tier was {expect}.")
        return 0

    print(
        "\nFAIL: no rate_limit_exceeded/metric=rpm. "
        "Possible causes: rpm=0 (unlimited) on PlanLimits, "
        "deploy without rate_limit.admit, or burst too small — try "
        "`--extra 80` / wait for a fresh minute."
    )
    return 1


def probe_itpm(expect: int) -> int:
    client = _client()
    chars = max(expect * 5, 8_000)
    blob = ("word " * (chars // 5))[:chars]
    print(f"ITPM probe: one create with ~{chars} char prompt (expect itpm ≈ {expect})")
    try:
        client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=1,
            messages=[{"role": "user", "content": blob}],
        )
        print("FAIL: request succeeded — ITPM ceiling higher, unlimited, or estimate low.")
        return 1
    except RateLimitError as e:
        body = _rl_body(e)
        print(
            f"429 code={body.get('code') or e.code} metric={body.get('metric')} "
            f"used={body.get('used')}/{body.get('limit')}"
        )
        if body.get("metric") == "itpm":
            print("OK: ITPM refuse on oversized admit.")
            return 0
        print("FAIL: 429 but metric is not itpm.")
        return 1
    except VioletError as e:
        print(f"ERR: {e}")
        return 1


def probe_otpm(expect: int) -> int:
    client = _client()
    max_tokens = expect + 1
    print(f"OTPM probe: one create with max_tokens={max_tokens} (expect otpm ≈ {expect})")
    try:
        client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": "hi"}],
        )
        print("FAIL: request succeeded — OTPM unlimited or higher than expected.")
        return 1
    except RateLimitError as e:
        body = _rl_body(e)
        print(
            f"429 code={body.get('code') or e.code} metric={body.get('metric')} "
            f"used={body.get('used')}/{body.get('limit')}"
        )
        if body.get("metric") == "otpm":
            print("OK: OTPM refuse on max_tokens reserve.")
            return 0
        print("FAIL: 429 but metric is not otpm (maybe RPM/ITPM already exhausted).")
        return 1
    except VioletError as e:
        print(f"ERR: {e}")
        return 1


def probe_auth_ip(expect: int = 20) -> int:
    base = (os.environ.get("MESSAGES_API_URL") or DEFAULT_BASE).rstrip("/")
    n = expect + 5
    print(f"Auth IP probe: {n} GET {base}/api/auth/profile (expect AUTH_IP_RPM ≈ {expect})\n")
    for i in range(1, n + 1):
        req = urllib.request.Request(
            f"{base}/api/auth/profile",
            headers={"Accept": "application/json", "User-Agent": "violet-rate-limit-probe/1"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                print(f"  [{i:3d}] {resp.status}")
        except urllib.error.HTTPError as e:
            body_raw = e.read() or b""
            try:
                body = json.loads(body_raw) if body_raw else {}
            except ValueError:
                body = {}
            code = body.get("code") if isinstance(body, dict) else None
            print(f"  [{i:3d}] {e.code}  code={code}")
            if e.code == 429 and code == "auth_rate_limit_exceeded":
                print(f"\nOK: auth IP RPM tripped on request #{i} (expect ~#{expect + 1}).")
                return 0
        except Exception as exc:  # noqa: BLE001
            print(f"  [{i:3d}] ERR {exc}")
            return 1
    print("\nFAIL: no auth_rate_limit_exceeded — IP limiter off or higher ceiling.")
    return 1


def main() -> None:
    p = argparse.ArgumentParser(description="Probe Violet workspace / auth rate limits")
    p.add_argument("mode", choices=("rpm", "itpm", "otpm", "auth"))
    p.add_argument("--expect", type=int, default=0)
    p.add_argument(
        "--tier",
        choices=tuple(TIER_RPM),
        default=os.environ.get("RATE_LIMIT_TIER", "Free"),
    )
    p.add_argument(
        "--workers",
        type=int,
        default=0,
        help="RPM burst thread count (default: request count)",
    )
    p.add_argument(
        "--extra",
        type=int,
        default=40,
        help="RPM burst: requests above --expect (default 40)",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Per-request timeout seconds for RPM burst (default 120)",
    )
    args = p.parse_args()

    if args.mode == "auth":
        expect = args.expect or int(os.environ.get("AUTH_IP_RPM", "20"))
        raise SystemExit(probe_auth_ip(expect))

    if args.mode == "rpm":
        expect = args.expect or int(os.environ.get("RATE_LIMIT_EXPECT_RPM", TIER_RPM[args.tier]))
        workers = args.workers or (expect + args.extra)
        raise SystemExit(
            probe_rpm(expect, workers=workers, extra=args.extra, timeout=args.timeout)
        )
    if args.mode == "itpm":
        expect = args.expect or int(os.environ.get("RATE_LIMIT_EXPECT_ITPM", TIER_ITPM[args.tier]))
        raise SystemExit(probe_itpm(expect))
    if args.mode == "otpm":
        expect = args.expect or int(os.environ.get("RATE_LIMIT_EXPECT_OTPM", TIER_OTPM[args.tier]))
        raise SystemExit(probe_otpm(expect))


if __name__ == "__main__":
    main()
