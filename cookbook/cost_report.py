"""Recipe: pre-flight token estimate, then post-call actuals.

Before spending anything, `count_tokens(...)` tells you how many input tokens a
request will cost. We print that estimate, run the real `create(...)`, then show
the *actual* usage the gateway billed. Finally, if your workspace exposes
metering, we pull a `usage.get(...)` rollup for the last 24h. No prices here on
purpose — just token counts you can multiply by your own rate card.

Run (from the repo root, package installed or on PYTHONPATH):

    python cookbook/cost_report.py

Prereqs: a paid workspace (Auth0 login or `VIOLET_API_KEY`); the first
call opens a one-time browser login; the gateway sets the model.
"""
import os

from violet import APIError, Violet, VioletError

# Placeholder — API requires model=; gateway sets the served model (client cannot).
DEFAULT_MODEL = os.environ.get("MESSAGES_TEST_MODEL", "Violet Test Model")

PROMPT = (
    "In two sentences, explain why counting tokens before a call is useful "
    "for budgeting an LLM application."
)


def preflight(client: Violet, model: str, messages: list[dict]) -> int | None:
    """Return the estimated input-token count for `messages` (or None on error)."""
    try:
        tc = client.messages.count_tokens(model=model, messages=messages)
    except APIError as e:
        print(f"[count_tokens api error {e.status_code}: {e.message}]")
        return None
    return tc.input_tokens


def workspace_rollup(client: Violet) -> None:
    """Print the workspace's monthly billing snapshot if metering is available.

    Violet's `usage.get()` reports USD spend for the current billing period (not
    per-call token totals) — the spend side of the budgeting picture, alongside
    the per-call token counts above."""
    try:
        rpt = client.usage.get()
    except VioletError as e:
        print(f"\nworkspace usage unavailable: {e}")
        return
    print("\n-- workspace usage (current billing month) --")
    print(f"  spend:     {rpt.usage_monthly_usd} USD")
    print(f"  limit:     {rpt.limit_monthly_usd} USD")
    print(f"  remaining: {rpt.limit_remaining_usd} USD")
    print(f"  resets at: {rpt.resets_at}")


def main() -> None:
    client = Violet()
    model = DEFAULT_MODEL
    messages = [{"role": "user", "content": PROMPT}]

    # 1) Pre-flight: estimate input cost before committing to the call.
    estimated_in = preflight(client, model, messages)
    if estimated_in is not None:
        print(f"estimated input tokens: {estimated_in}")

    # 2) The real call.
    try:
        msg = client.messages.create(model=model, max_tokens=200, messages=messages)
    except APIError as e:
        print(f"[create api error {e.status_code}: {e.message}]")
        return
    except VioletError as e:
        print(f"[error: {e}]")
        return

    print("\nreply:", msg.text())

    # 3) Post-call actuals — compare the estimate against what was billed.
    actual_in = msg.usage.input_tokens
    print("\n-- this call --")
    print(f"  input tokens (actual):   {actual_in}")
    print(f"  output tokens (actual):  {msg.usage.output_tokens}")
    if estimated_in is not None:
        print(f"  input estimate vs actual: {estimated_in} -> {actual_in} "
              f"(delta {actual_in - estimated_in:+d})")

    # 4) Optional: a workspace-wide rollup for context.
    workspace_rollup(client)


if __name__ == "__main__":
    main()
