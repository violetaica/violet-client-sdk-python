"""Workspace metering — `client.usage.get()` (a Violet extension).

Returns the workspace's **monthly billing snapshot** (USD spend, the plan limit,
remaining budget, and the reset date) from the workspace billing snapshot. This
is Violet-specific (monthly USD, not per-request token totals) — and it covers
the current billing period (anchored to signup), not an arbitrary window, so it
takes no `from`/`to`.

        python -m examples.usage

Prereqs: paid workspace  a one-time
browser login on first call, and a gateway-set model (client model= is a placeholder).
"""
from examples._common import make_client

from violet import APIError, VioletError


def main() -> None:
    client = make_client()

    try:
        rpt = client.usage.get()
    except APIError as e:
        print(f"API error {e.status_code}: {e.message}")
        return
    except VioletError as e:
        print(f"Error: {e}")
        return

    print(f"workspace:        {rpt.get('workspace_id')}")
    print(f"spend (month):    {rpt.usage_monthly_usd} USD")
    print(f"limit (month):    {rpt.limit_monthly_usd} USD")
    print(f"remaining:        {rpt.limit_remaining_usd} USD")
    print(f"resets at:        {rpt.resets_at}")


if __name__ == "__main__":
    main()
