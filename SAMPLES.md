# Violet SDK — samples catalog

Runnable, **original** sample apps built against `from violet import Violet` —
implementations of common LLM-app patterns (original Violet samples).
Three tiers: per-method `examples/`, technique `cookbook/` recipes, and one
full-stack `webchat/` showcase.

## Prerequisites (all samples)

- A provisioned **paid workspace** (defaults to production gateway).
- **Auth:** either a one-time browser login (Auth0 PKCE; token cached at
`~/.violet/credentials.json`) **or** a workspace API key via
`$env:VIOLET_API_KEY` (Settings → API keys). `python -m examples.whoami`
diagnoses Auth0 provisioning if a call returns 403; `examples.api_key_chat`
is the CI path.
- Samples pass `model="Violet Test Model"` only because `messages.create` requires the `model=` argument — the **gateway sets the served model**;
the client cannot choose it. Override the placeholder via `MESSAGES_TEST_MODEL`
if you want.
- Install the SDK first: `pip install -e .` (from the repo root).



## `examples/` — one SDK method each

Run from the repo root as `python -m examples.<name>`.


| Sample              | Shows                                    | SDK surface                                            |
| ------------------- | ---------------------------------------- | ------------------------------------------------------ |
| `chat`              | non-streaming chat                       | `messages.create` → `msg.content[0].text`              |
| `api_key_chat`      | CI path with `VIOLET_API_KEY` (no Auth0) | `Violet(api_key=…)` → `messages.create`                |
| `stream`            | live token streaming                     | `messages.stream` → `text_stream`, `get_final_message` |
| `count_tokens`      | pre-flight prompt size                   | `messages.count_tokens`                                |
| `structured_output` | schema-constrained JSON                  | `messages.parse` → `parsed_output`                     |
| `tool_use`          | local tool loop (calculator)             | tool blocks via `raw_passthrough`                      |
| `usage`             | monthly billing snapshot                 | `usage.get()` (Violet extension)                       |
| `whoami`            | diagnose auth/provisioning               | `auth.login`                                           |
| `logout`            | sign out                                 | `auth.logout`                                          |
| `rate_limit_probe`  | RPM / ITPM / OTPM burst probe            | concurrent `messages.create` + 429 metrics             |




## `cookbook/` — one technique per recipe

Run from the repo root as `python cookbook/<name>.py`.


| Recipe               | Technique                                                      |
| -------------------- | -------------------------------------------------------------- |
| `streaming_repl`     | multi-turn streaming chat REPL (history threading)             |
| `extract_structured` | structured extraction over messy text → typed dict             |
| `classify_batch`     | bulk classification (sequential loop; portable to any gateway) |
| `rag_minimal`        | keyword-retrieval RAG with **no** external vector DB           |
| `tool_agent`         | raw-passthrough agent: safe calculator + sandboxed file read   |
| `cost_report`        | pre-flight estimate vs actual tokens + monthly spend           |




## `webchat/` — full-stack showcase

A FastAPI + Server-Sent-Events chat where the backend holds the Auth0 token
**server-side** and streams the SDK's output to the browser (the token never
reaches the page).

```powershell
pip install -e ".[web]"      # one-time: fastapi + uvicorn
python -m webchat.app        # http://127.0.0.1:8000
```

See [webchat/README.md](webchat/README.md).

## Backend-reality caveats (important)

A few endpoints behave
differently on the **Violet gateway** (`api.violetai.ca` /
`hermes_agent_v2`). The samples handle these honestly:

- **Usage** — `GET /api/v1/usage/me`, a **monthly USD** billing snapshot (not
request/token totals), no time window.

- **Tool loops** require `Violet(raw_passthrough=True)` — and on that path the
gateway **dictates the model** (`model=` is ignored server-side).
- **Structured output** — the gateway may not enforce `output_config`; the
structured recipes also ask for JSON in the prompt so they work either way.



## Testing without a backend

`from violet import fixtures` emits response fixtures, and
`tests/test_shapes.py` round-trips them through the SDK parsers — run
`PYTHONPATH=. python tests/test_shapes.py` from the repo root.