"""Recipe: structured extraction with a JSON schema.

Pulls typed fields out of free-form text (here, short contact / invoice blurbs)
by handing the gateway a JSON schema and letting `messages.parse` constrain the
model's output to it. The result is a real Python dict you can index — no regex,
no brittle string parsing.

Run (from the repo root, package installed or on PYTHONPATH):

    python cookbook/extract_structured.py

Prereqs: a paid workspace (Auth0 login or `VIOLET_API_KEY`), a
one-time browser login on the first call, The gateway sets the model
(env MESSAGES_TEST_MODEL, e.g. gateway default).
"""
import os

from violet import APIError, Violet, VioletError

# Placeholder — API requires model=; gateway sets the served model (client cannot).
DEFAULT_MODEL = os.environ.get("MESSAGES_TEST_MODEL", "Violet Test Model")

# A few messy, real-world-ish blurbs to extract from.
SAMPLES = [
    "Hi, I'm Dana Okafor, reach me at dana.okafor@example.com or +1 (415) 555-0137. "
    "I run ops at Northwind Trading.",
    "Invoice #A-2231 for ACME Robotics: 3 line items, total due $4,820.00, net 30. "
    "Contact billing@acme.example for questions.",
    "ping me — sam (no last name given), samb@startup.io, we're a 12-person team.",
]

# The shape we want back. `additionalProperties: False` keeps the model honest;
# `required` makes the must-have fields explicit. Optional fields are nullable.
SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "person or contact name"},
        "email": {"type": "string", "description": "email address if present"},
        "phone": {"type": ["string", "null"], "description": "phone, else null"},
        "organization": {"type": ["string", "null"]},
        "amount_due": {
            "type": ["number", "null"],
            "description": "numeric total in USD if this is an invoice, else null",
        },
    },
    "required": ["name", "email"],
    "additionalProperties": False,
}


def extract(client: Violet, text: str) -> dict:
    """Constrain the model to SCHEMA and return the parsed dict."""
    msg = client.messages.parse(
        model=DEFAULT_MODEL,
        max_tokens=512,
        output_format=SCHEMA,
        system="Extract the requested fields from the user's text. Reply with "
        "ONLY a JSON object (no prose). Use null for anything not present. Do "
        "not invent values.",
        messages=[{"role": "user", "content": text}],
    )
    return msg.parsed_output


def main() -> None:
    client = Violet()
    print(f"gateway healthy: {client.health()}\n")

    for i, text in enumerate(SAMPLES, start=1):
        print(f"--- sample {i} ---")
        print(f"text: {text}")
        try:
            parsed = extract(client, text)
        except APIError as e:
            print(f"API error {e.status_code}: {e.message}\n")
            continue
        except VioletError as e:
            print(f"error: {e}\n")
            continue

        if not isinstance(parsed, dict):
            print(f"could not parse structured output (got {parsed!r})\n")
            continue

        print(f"parsed: {parsed}")

        # Validate one required field is actually present and non-empty. The
        # schema asks for it, but we never trust the model blindly.
        email = parsed.get("email")
        if email:
            print(f"validated: email present -> {email}\n")
        else:
            print("validated: WARNING email missing from extraction\n")


if __name__ == "__main__":
    main()
