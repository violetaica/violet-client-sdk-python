"""Recipe: an interactive, multi-turn streaming chat REPL.

Reads a line from you, streams the assistant's reply token-by-token, then folds
that reply back into the conversation history so the next turn has full context.
Type a message and press Enter; Ctrl-D (EOF) or Ctrl-C exits cleanly. Send an
empty line to skip. Type `/reset` to start a fresh conversation, `/quit` to exit.

Run (from the repo root, package installed or on PYTHONPATH):

    python cookbook/streaming_repl.py

Prereqs: a paid workspace (Auth0 login or `VIOLET_API_KEY`); the first
call opens a one-time browser login; the gateway sets the model.
"""
import os
import sys

from violet import APIError, Violet, VioletError

# Placeholder — API requires model=; gateway sets the served model (client cannot).
DEFAULT_MODEL = os.environ.get("MESSAGES_TEST_MODEL", "Violet Test Model")

SYSTEM = "You are a concise, friendly assistant. Keep replies short unless asked."


def prompt() -> str:
    """Read one user line; raise EOFError on Ctrl-D so the loop can exit."""
    sys.stdout.write("\nyou> ")
    sys.stdout.flush()
    return input()


def stream_reply(client: Violet, model: str, history: list[dict]) -> str | None:
    """Stream one assistant turn to stdout; return its full text (or None on error)."""
    sys.stdout.write("bot> ")
    sys.stdout.flush()
    try:
        with client.messages.stream(model=model, max_tokens=512,
                                     system=SYSTEM, messages=history) as stream:
            for token in stream.text_stream:
                sys.stdout.write(token)
                sys.stdout.flush()
            final = stream.get_final_message()
    except APIError as e:
        sys.stdout.write(f"\n[api error {e.status_code}: {e.message}]\n")
        return None
    except VioletError as e:
        sys.stdout.write(f"\n[error: {e}]\n")
        return None
    sys.stdout.write("\n")
    return final.text()


def main() -> None:
    client = Violet()
    model = DEFAULT_MODEL
    history: list[dict] = []
    print(f"Streaming REPL on {model!r}. Ctrl-D or /quit to exit; /reset to clear.")

    while True:
        try:
            line = prompt()
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            break

        line = line.strip()
        if not line:
            continue
        if line in ("/quit", "/exit"):
            print("bye")
            break
        if line == "/reset":
            history.clear()
            print("[history cleared]")
            continue

        # Append the user turn, then stream the assistant turn.
        history.append({"role": "user", "content": line})
        reply = stream_reply(client, model, history)
        if reply is None:
            # The turn failed — drop the dangling user message so we can retry.
            history.pop()
            continue
        # Persist the assistant reply so the next turn is contextual.
        history.append({"role": "assistant", "content": reply})


if __name__ == "__main__":
    main()
