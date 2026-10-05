"""Recipe: bulk classification of many short texts.

Classifies a list of texts by asking the model for one label each, using a
simple sequential loop of `messages.create(...)` calls.

Run (from the repo root, package installed or on PYTHONPATH):

    python cookbook/classify_batch.py

Prereqs: a paid workspace (Auth0 login or `VIOLET_API_KEY`), a
one-time browser login on the first call, The gateway sets the model
(env MESSAGES_TEST_MODEL, e.g. gateway default).
"""
import os

from violet import APIError, Violet, VioletError

# Placeholder — API requires model=; gateway sets the served model (client cannot).
DEFAULT_MODEL = os.environ.get("MESSAGES_TEST_MODEL", "Violet Test Model")

# Short texts to classify.
TEXTS = [
    "Absolutely loved the new update, everything feels faster!",
    "The app crashed three times today and I lost my work.",
    "Can you tell me what time your support line opens?",
    "Meh. It's fine I guess, nothing special either way.",
]

SYSTEM = (
    "You are a text classifier. Reply with EXACTLY ONE lowercase word that is "
    "the sentiment of the user's message: positive, negative, neutral, or "
    "question. No punctuation, no explanation."
)

VALID = {"positive", "negative", "neutral", "question"}


def classify(client: Violet, text: str) -> str:
    """One classification call -> a single-word label (best-effort normalized)."""
    msg = client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=8,
        system=SYSTEM,
        messages=[{"role": "user", "content": text}],
    )
    raw = msg.text().strip().lower().strip(".!,") or "<empty>"
    # Keep only the first token; fall back to the raw word if it's off-list.
    word = raw.split()[0] if raw.split() else raw
    return word if word in VALID else raw


def main() -> None:
    client = Violet()

    results: list[tuple[str, str]] = []
    for text in TEXTS:
        try:
            label = classify(client, text)
        except APIError as e:
            label = f"<api error {e.status_code}>"
        except VioletError as e:
            print(f"stopping: {e}")
            return
        results.append((label, text))

    print("classifications:")
    for label, text in results:
        print(f"  [{label}]  {text}")


if __name__ == "__main__":
    main()
