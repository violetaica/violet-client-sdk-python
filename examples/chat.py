"""Chat call via `client.messages.create(...)`.

        python -m examples.chat

On first run you sign in once (browser PKCE); the token is cached. See the
README "Provisioning & 403s" and "Cloudflare 1010" sections if it errors.
"""
import os

from violet import APIError, Violet, VioletError

# Placeholder — API requires model=; gateway sets the served model (client cannot).
MODEL = os.environ.get("MESSAGES_TEST_MODEL", "Violet Test Model")


def main() -> None:
    client = Violet()  # auth + base url + workspace resolved from env / defaults

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

    # Read the reply:
    print("reply:", msg.content[0].text)
    print(f"tokens: in={msg.usage.input_tokens} out={msg.usage.output_tokens}")
    print(f"stop_reason: {msg.stop_reason}")


if __name__ == "__main__":
    main()
