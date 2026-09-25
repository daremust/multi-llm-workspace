# Multi-LLM Workspace

A local-first, single-user Streamlit workspace. Milestone 3 adds explicit OpenRouter model selection and parallel generation to the existing project inputs and manual responses.

## Run

Use the existing Python 3.12 environment:

```sh
.venv/bin/python -m streamlit run app/ui.py
```

Set `OPENROUTER_API_KEY` in your environment or a local `.env` file, following `.env.example`. `.env` is ignored by Git. A key is required for generation, including free models, but not for local project work, saved results, or the public catalog. Never commit a real key.

1. Create/open a project and save source material, task, and output format.
2. Load the model catalog, optionally filter for free token pricing, and select models yourself.
3. Review the call count, estimate, saved-input preview, and output-token limit.
4. Click **Generate**. Saved inputs are sent to OpenRouter; unsaved widget edits and manual responses are excluded.
5. Inspect independent outcomes and previous runs. Rerunning or restarting Streamlit does not generate again.

## Catalog and estimates

The authoritative catalog is `GET https://openrouter.ai/api/v1/models`. It loads on demand, is cached in memory for 15 minutes, and displays its fetch timestamp. Explicit refresh clears that cache. There is no hard-coded recommendation list or model substitution. The selector includes text-capable models and excludes OpenRouter routing aliases and `:online` / `:latest` aliases; automatic model selection and web search are outside this milestone.

Free token status comes from both catalog prompt and completion prices being exactly zero, not a model's name or suffix. Unknown, invalid, or missing prices remain unknown. The token estimate uses `ceil(total UTF-8 message bytes / 4) + 8 × message count` input tokens and the full configured output-token limit (default 1,024) for each model. It applies per-token catalog prices and reported request fees. Missing request fees or unmodeled nonzero/unknown fees make the total unknown, even if the token component is free. No cache discount is assumed.

This heuristic is **not a tokenizer or spending cap**. Actual tokenization, output length, reasoning, provider routing, caching, fees, and price changes can differ. Free models can still require an API key and be rate limited. Provider-reported response usage/cost is persisted separately from estimates; absent values remain SQL `NULL`.

API details follow OpenRouter's [model catalog documentation](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties), [chat completion documentation](https://openrouter.ai/docs/api/api-reference/chat/create-a-chat-completion), and [usage accounting documentation](https://openrouter.ai/docs/cookbook/administration/usage-accounting).

## Generation and persistence

- `app/openrouter.py`: catalog parsing and an `httpx` OpenRouter gateway. No direct-provider adapters or automatic retries.
- `app/generation.py`: prompting, estimates, saved-input snapshots, and `httpx.AsyncClient` requests with a maximum of three concurrent calls. One chosen model produces one non-streaming POST. Gateway provider fallbacks are disabled. Temperature defaults to 0.7 and is omitted when the catalog explicitly does not support it. Exact per-model payloads are stored.
- `app/db.py`: the existing schema remains at version 1. A run and its pending requests are committed before dispatch. An atomic run claim prevents redispatch of the same run. Each completion and its response commit together, independently of other requests.
- `app/generation_ui.py`: compact selection/estimation controls and two-column result inspection with run history. No ranking, ratings, winner selection, or editing tools.

The existing `responses.verified_provider` column identifies the verified **gateway** (`openrouter`), not a model author or upstream host. `verified_model` contains the API-reported model, or null if absent. Generation metadata separately records requested/reported model IDs, catalog namespace/name, and upstream provider if reported. A namespace is only a catalog label, not verified authorship. Manual responses retain their original unverified provenance and have no API request linkage.

Timeouts, transport errors, and malformed outcomes remain visibly uncertain using the existing `interrupted` status. HTTP/API errors persist as failures; successful siblings remain available and the run becomes partial. There are no automatic paid retries, fallback models, or follow-up metadata requests. A hard process stop may leave a run marked pending/running; the UI labels it unfinished/possibly uncertain and never resumes it automatically. A completion lost before its database commit cannot be reconstructed locally.

Dedicated historical-response regeneration is deferred: exact historical replay versus current saved inputs needs an explicit user choice and a fresh estimate. In this milestone, select only the desired model and click Generate to make a new one-model run using current saved inputs.

## Tests

```sh
.venv/bin/python -m pytest -q
```

Tests use temporary SQLite databases and mocked HTTP responses. The test suite blocks outbound network access; it requires no real API key and makes no live model calls.
