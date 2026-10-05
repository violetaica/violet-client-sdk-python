"""CI-friendly chat call using a workspace API key (no Auth0 browser).

Prerequisites:
  1. Settings → API keys → Create (Owner/Admin) — copy the ``vio_sk_…`` once.
  2. Export:
       $env:VIOLET_API_KEY = "vio_sk_…"
       $env:VIOLET_WORKSPACE_ID = "ws_…"   # optional; key is workspace-bound

        python -m examples.api_key_chat
"""
import os

from violet import APIError, Violet, VioletError

# Placeholder — API requires model=; gateway sets the served model (client cannot).
MODEL = os.environ.get("MESSAGES_TEST_MODEL", "Violet Test Model")


def main() -> None:
    if not (os.environ.get("VIOLET_API_KEY") or os.environ.get("api_key")):
        print("Set VIOLET_API_KEY to a workspace key from Settings → API keys.")
        return

    # api_key= / VIOLET_API_KEY skips Auth0 entirely.
    client = Violet()

    print(f"gateway healthy: {client.health()}")

    try:
        msg = client.messages.create(
            model=MODEL,
            max_tokens=64,
            messages=[{"role": "user", "content": "Say hello in 3 words. No punctuation."}],
        )
    except APIError as e:
        print(f"API error {e.status_code}: {e.message}")
        return
    except VioletError as e:
        print(f"Error: {e}")
        return

    print("reply:", msg.content[0].text)
    print(f"tokens: in={msg.usage.input_tokens} out={msg.usage.output_tokens}")


if __name__ == "__main__":
    main()
