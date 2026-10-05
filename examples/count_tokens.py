"""Count input tokens for a prompt BEFORE sending it — `client.messages.count_tokens(...)`.

Pass `model` + `messages` (and optionally
`system`/`tools`) you'd send to `create(...)`, and get back the prompt token count
without spending any output tokens. Handy for budgeting and context-window checks.

        python -m examples.count_tokens

Prereqs: paid workspace (Auth0 login or `VIOLET_API_KEY`), a one-time
browser login on first call, and a gateway-set model (client model= is a placeholder).
"""
from examples._common import DEFAULT_MODEL, make_client

from violet import APIError, VioletError


def main() -> None:
    client = make_client()

    messages = [
        {"role": "user", "content": "Summarize the plot of Moby-Dick in one sentence."},
    ]
    system = "You are a terse literary critic. Never exceed one sentence."

    try:
        count = client.messages.count_tokens(
            model=DEFAULT_MODEL,
            system=system,
            messages=messages,
        )
    except APIError as e:
        print(f"API error {e.status_code}: {e.message}")
        return
    except VioletError as e:
        print(f"Error: {e}")
        return

    print(f"model:        {DEFAULT_MODEL}")
    print(f"input_tokens: {count.input_tokens}")
    print("(this is the prompt size only — no generation was requested)")


if __name__ == "__main__":
    main()
