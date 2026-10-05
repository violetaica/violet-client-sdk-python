"""Raw-passthrough tool-use agent with two local tools.

What this shows
---------------
A small agent that runs the full tool-use loop against the Violet gateway's
*raw passthrough* path. The model is given two locally-executed tools and the
script drives the create -> tool_use -> execute -> tool_result cycle until the
model produces a final text answer (iteration-capped).

Tools:
  * calculator      — evaluate a simple arithmetic expression safely.
  * read_text_file  — read a text file, capped in size, refusing any path that
                      escapes the current working directory.

CAVEAT — raw_passthrough / server-dictated model
-------------------------------------------------
The tool-use loop requires `Violet(raw_passthrough=True)`. On that stateless
path the GATEWAY dictates which model handles the request, so the `model=` we
pass is effectively ignored server-side. We still pass it for clarity / parity.

How to run
----------
        python cookbook/tool_agent.py

Prereqs: a paid Violet workspace (Auth0 login or `VIOLET_API_KEY`), a
one-time browser login on the first call, The gateway sets the model.
"""
import ast
import operator
import os

from violet import APIError, Violet, VioletError

# Placeholder — API requires model=; gateway sets the served model (client cannot).
DEFAULT_MODEL = os.environ.get("MESSAGES_TEST_MODEL", "Violet Test Model")

MAX_ITERATIONS = 5          # hard cap on tool-use rounds
MAX_FILE_BYTES = 4096       # read_text_file size cap

TOOLS = [
    {
        "name": "calculator",
        "description": "Evaluate a basic arithmetic expression (+, -, *, /, **, parentheses).",
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "An arithmetic expression, e.g. '2 * (3 + 4)'.",
                }
            },
            "required": ["expression"],
        },
    },
    {
        "name": "read_text_file",
        "description": (
            "Read a small UTF-8 text file located under the current working directory. "
            f"Output is truncated to {MAX_FILE_BYTES} bytes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to a text file under the working directory.",
                }
            },
            "required": ["path"],
        },
    },
]

# Only these AST node / operator types are permitted in the calculator.
_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _eval_node(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval_node(node.operand))
    raise ValueError("unsupported expression")


def calculator(expression):
    """Safely evaluate arithmetic by walking a parsed AST (no eval/exec)."""
    try:
        tree = ast.parse(str(expression), mode="eval")
        return str(_eval_node(tree.body))
    except Exception as e:  # noqa: BLE001 — surface a tool-side error string
        return f"Error: could not evaluate ({e})."


def read_text_file(path):
    """Read a text file, refusing paths outside the working directory."""
    cwd = os.path.realpath(os.getcwd())
    target = os.path.realpath(os.path.join(cwd, str(path)))
    # Containment check: target must live under cwd.
    if os.path.commonpath([cwd, target]) != cwd:
        return "Error: refused — path is outside the working directory."
    if not os.path.isfile(target):
        return "Error: file not found."
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            data = f.read(MAX_FILE_BYTES + 1)
    except OSError as e:
        return f"Error: could not read file ({e})."
    if len(data) > MAX_FILE_BYTES:
        return data[:MAX_FILE_BYTES] + "\n...[truncated]"
    return data


TOOL_IMPLS = {"calculator": calculator, "read_text_file": read_text_file}


def run_tool(name, tool_input):
    impl = TOOL_IMPLS.get(name)
    if impl is None:
        return f"Error: unknown tool '{name}'."
    # tool_input is dict-like; pull the schema fields by key.
    if name == "calculator":
        return impl(tool_input["expression"])
    return impl(tool_input["path"])


def main():
    # raw_passthrough=True is REQUIRED for the local tool-use loop. Note that on
    # this path the gateway dictates the model server-side (see module docstring).
    client = Violet(raw_passthrough=True)

    messages = [{
        "role": "user",
        "content": (
            "First compute 17 * 23 + 5 using the calculator tool, then tell me the "
            "result in a sentence."
        ),
    }]

    final = None
    for _ in range(MAX_ITERATIONS):
        try:
            msg = client.messages.create(
                model=DEFAULT_MODEL,
                max_tokens=1024,
                messages=messages,
                tools=TOOLS,
            )
        except APIError as e:
            print(f"API error {e.status_code}: {e.message}")
            return
        except VioletError as e:
            print(f"Error: {e}")
            return

        tool_uses = [b for b in msg.content if b.type == "tool_use"]
        if msg.stop_reason != "tool_use" or not tool_uses:
            final = msg
            break

        # Echo the assistant turn (tool_use blocks) back into the transcript...
        messages.append({"role": "assistant", "content": [b.to_dict() for b in msg.content]})
        # ...then run each tool locally and return tool_result blocks.
        results = []
        for tu in tool_uses:
            print(f"[tool] {tu.name}({dict(tu.input.items())})")
            results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": run_tool(tu.name, tu.input),
            })
        messages.append({"role": "user", "content": results})
    else:
        print("Stopped: hit the iteration cap without a final answer.")
        return

    print("\nFinal answer:")
    print(final.text())


if __name__ == "__main__":
    main()
