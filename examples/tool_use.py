"""Tool use — a minimal raw-passthrough agent loop with a local calculator.

The gateway only emits `tool_use` blocks for YOU to run locally (and accepts your
`tool_result` back) on the raw-passthrough path. NOTE: on this path the gateway
DICTATES the model server-side — the `model` you pass is ignored — so the loop
below works regardless of which provider model the gateway selects.

        python -m examples.tool_use

Prereqs: paid workspace (Auth0 login or `VIOLET_API_KEY`), a one-time
browser login on first call, and a gateway-set model (client model= is a placeholder).
"""
import operator

from violet import APIError, VioletError

from ._common import DEFAULT_MODEL, make_client

_OPS = {"add": operator.add, "subtract": operator.sub,
        "multiply": operator.mul, "divide": operator.truediv}

TOOLS = [
    {
        "name": "calculator",
        "description": "Evaluate a single arithmetic operation on two numbers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": list(_OPS)},
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["op", "a", "b"],
        },
    }
]

MAX_ITERS = 5


def run_calculator(args) -> str:
    fn = _OPS[args["op"]]
    return str(fn(args["a"], args["b"]))


def main() -> None:
    # raw_passthrough=True: gateway forwards our tools and returns tool_use blocks
    # for us to execute. The model is server-dictated on this path (see header).
    client = make_client(raw_passthrough=True)

    messages = [
        {"role": "user", "content": "What is 17 * 24, then add 6 to that? Use the calculator."}
    ]

    try:
        for _ in range(MAX_ITERS):
            msg = client.messages.create(
                model=DEFAULT_MODEL, max_tokens=1024, messages=messages, tools=TOOLS
            )
            tool_uses = [b for b in msg.content if b.type == "tool_use"]
            if msg.stop_reason != "tool_use" or not tool_uses:
                print("final:", msg.text().strip())
                return

            # Echo the assistant turn back, then answer each tool_use.
            messages.append({"role": "assistant", "content": [b.to_dict() for b in msg.content]})
            results = []
            for tu in tool_uses:
                out = run_calculator(tu.input)
                print(f"  tool {tu.name}({dict(tu.input.items())}) -> {out}")
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": out})
            messages.append({"role": "user", "content": results})

        print("stopped: hit MAX_ITERS without a final answer.")
    except APIError as e:
        print(f"API error {e.status_code}: {e.message}")
    except VioletError as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()
