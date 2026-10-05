"""Structured output — constrain the model to a JSON schema and parse it.

Uses `client.messages.parse(output_format=<json schema dict>)`, which mirrors
`messages.parse`: the gateway constrains generation to the schema and
the returned message exposes the parsed object as `msg.parsed_output`.

        python -m examples.structured_output

Prereqs: paid workspace (Auth0 login or `VIOLET_API_KEY`), a one-time
browser login on first call, and a gateway-set model (client model= is a placeholder).
"""
from violet import APIError, VioletError

from ._common import DEFAULT_MODEL, make_client

SENTENCE = (
    "Hi, I'm Ada Lovelace from Analytical Engines Inc.; you can reach me at "
    "ada@analytical.example, and we're on the Enterprise plan."
)

# A strict JSON schema. additionalProperties:False keeps the model to our fields.
SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "email": {"type": "string"},
        "company": {"type": "string"},
        "plan": {"type": "string", "enum": ["Free", "Pro", "Enterprise"]},
    },
    "required": ["name", "email", "company", "plan"],
    "additionalProperties": False,
}


def main() -> None:
    client = make_client()

    try:
        msg = client.messages.parse(
            model=DEFAULT_MODEL,
            max_tokens=256,
            output_format=SCHEMA,
            messages=[
                {
                    # parse() sets output_config to constrain generation; we also ask
                    # for JSON in the prompt so it still works if a gateway doesn't
                    # enforce the schema server-side.
                    "role": "user",
                    "content": (
                        "Extract the contact fields from this sentence and reply with "
                        "ONLY a JSON object with keys name, email, company, plan — no "
                        "prose:\n\n" + SENTENCE
                    ),
                }
            ],
        )
    except APIError as e:
        print(f"API error {e.status_code}: {e.message}")
        return
    except VioletError as e:
        print(f"Error: {e}")
        return

    parsed = msg.parsed_output
    if parsed is None:
        print("Model did not return parseable JSON. Raw text:")
        print(msg.text())
        return

    print("parsed_output:", parsed)
    for field in ("name", "email", "company", "plan"):
        print(f"  {field:8} = {parsed.get(field)!r}")


if __name__ == "__main__":
    main()
