"""Streaming via `with client.messages.stream(...) as s`.

        python -m examples.stream
"""
import os
import sys

from violet import Violet

# Placeholder — API requires model=; gateway sets the served model (client cannot).
MODEL = os.environ.get("MESSAGES_TEST_MODEL", "Violet Test Model")


def main() -> None:
    client = Violet()
    with client.messages.stream(
        model=MODEL,
        max_tokens=128,
        messages=[{"role": "user", "content": "Write one short sentence about violets."}],
    ) as stream:
        for text in stream.text_stream:
            sys.stdout.write(text)
            sys.stdout.flush()
        final = stream.get_final_message()
    print(f"\n[stop_reason={final.stop_reason} out_tokens={final.usage.output_tokens}]")


if __name__ == "__main__":
    main()
