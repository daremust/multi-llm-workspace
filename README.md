# Multi-LLM Workspace

A local-first, single-user Streamlit workspace. Milestones 1–5 support project inputs, OpenRouter generation, local human review/editing, and explicit optional compute.

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

The authoritative catalog is `GET https://openrouter.ai/api/v1/models`. It loads on demand, is cached in memory for 15 minutes, and displays its fetch timestamp. The loaded catalog remains available for offline review reruns; generation requires a catalog younger than 15 minutes. Only explicit Load / refresh fetches catalog data, and it clears the cache. There is no hard-coded recommendation list or model substitution. The selector includes text-capable models and excludes OpenRouter routing aliases and `:online` / `:latest` aliases; automatic model selection and web search are outside this milestone.

Free token status comes from both catalog prompt and completion prices being exactly zero, not a model's name or suffix. Unknown, invalid, or missing prices remain unknown. The token estimate uses `ceil(total UTF-8 message bytes / 4) + 8 × message count` input tokens and the full configured output-token limit (default 1,024) for each model. It applies per-token catalog prices and reported request fees. Missing request fees or unmodeled nonzero/unknown fees make the total unknown, even if the token component is free. No cache discount is assumed.

This heuristic is **not a tokenizer or spending cap**. Actual tokenization, output length, reasoning, provider routing, caching, fees, and price changes can differ. Free models can still require an API key and be rate limited. Provider-reported response usage/cost is persisted separately from estimates; absent values remain SQL `NULL`.

API details follow OpenRouter's [model catalog documentation](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties), [chat completion documentation](https://openrouter.ai/docs/api/api-reference/chat/create-a-chat-completion), and [usage accounting documentation](https://openrouter.ai/docs/cookbook/administration/usage-accounting).

## Generation and persistence

- `app/openrouter.py`: catalog parsing and an `httpx` OpenRouter gateway. No direct-provider adapters or automatic retries.
- `app/generation.py`: prompting, estimates, saved-input snapshots, and `httpx.AsyncClient` requests with a maximum of three concurrent calls. One chosen model produces one non-streaming POST. Gateway provider fallbacks are disabled. Temperature defaults to 0.7 and is omitted when the catalog explicitly does not support it. Exact per-model payloads are stored.
- `app/db.py`: the existing schema remains at version 1. A run and its pending requests are committed before dispatch. An atomic run claim prevents redispatch of the same run. Each completion and its response commit together, independently of other requests.
- `app/generation_ui.py`: compact selection/estimation controls and run history, followed by local human comparison.

The existing `responses.verified_provider` column identifies the verified **gateway** (`openrouter`), not a model author or upstream host. `verified_model` contains the API-reported model, or null if absent. Generation metadata separately records requested/reported model IDs, catalog namespace/name, and upstream provider if reported. A namespace is only a catalog label, not verified authorship. Manual responses retain their original unverified provenance and have no API request linkage.

Timeouts, transport errors, and malformed outcomes remain visibly uncertain using the existing `interrupted` status. HTTP/API errors persist as failures; successful siblings remain available and the run becomes partial. There are no automatic paid retries, fallback models, or follow-up metadata requests. A hard process stop may leave a run marked pending/running; the UI labels it unfinished/possibly uncertain and never resumes it automatically. A completion lost before its database commit cannot be reconstructed locally.

Dedicated historical-response regeneration is deferred: exact historical replay versus current saved inputs needs an explicit user choice and a fresh estimate. In this milestone, select only the desired model and click Generate to make a new one-model run using current saved inputs.

## Tests

```sh
.venv/bin/python -m pytest -q
```

Tests use temporary SQLite databases and mocked HTTP responses. The test suite blocks outbound network access; it requires no real API key and makes no live model calls.

## Human review (Milestone 4)

Open any saved generation run to compare responses without an API key or new requests. One, two, or three responses appear in one desktop row. Larger runs scroll horizontally with a 20rem minimum column width (bounded by viewport width). Failed and uncertain requests retain their own columns. Compact summaries leave room for response text; Details preserves full provenance and unknown values.

- **Save rating** records an explicit human 1–5 star rating (or clears it). Ratings never reorder responses or select a primary.
- **Set as primary** records the human's choice for the project. There is no automatic initial choice, and selecting creates no version.
- **Working copy** starts with the primary response or its current saved version. Unsaved drafts survive reruns and project switching within the browser session; save a version before restarting.
- **Save version** creates an immutable manual edit linked to its source response and, where applicable, its base version, then makes it the current saved output. The original generated text and provenance never change.
- **Finish / Save** saves the working copy as the current version and confirms completion. Repeating Save/Finish on the unchanged current text creates no duplicate version or event. No separate finished state is needed.
- **Version history** lets the user inspect saved text and explicitly change the current version. Current saved output and primary-response selection are distinct: choosing a historical version does not silently change the primary response.

`app/review.py` performs transactional local mutations using existing schema version 1. Ratings use `responses.human_rating`; primary selection is a single lookup of the latest `selection_changed` entry in the existing minimal human-action history (no event replay). Versions use the existing derivation links and `projects.current_version_id`. Repeated unchanged actions are no-ops. `app/review_ui.py` presents comparison and editing without provider dependencies.

All review actions are local and make zero API calls, including catalog calls on reruns. Advanced responsive polish, diffs, branching graphs, critique, rewrite, merge, and fact checking remain outside Milestone 4.

## Optional compute (Milestone 5)

Open **Choose an optional operation** after generation/review. Choose the operation, its inputs, and the model yourself. Review **Additional API calls: 1**, the estimate, and the inspectable request before clicking its **Run** button. Loading a catalog is a separate explicit action. No optional operation is triggered by generation, viewing results, changing selections, saving a version, or rerunning Streamlit.

- **Rewrite:** use the current Working copy (including unsaved edits) or the current saved version, with optional instructions. The returned text is a candidate, not a replacement.
- **Merge:** explicitly select at least two distinct successful API draft responses or saved versions. Only those inputs are merged. Optional instructions are supported; agreement among models is not treated as evidence.
- **Critique:** use the chosen current draft and saved task/output context, with optional instructions. Structured qualitative feedback contains strengths, issues, factual/support concerns, clarity/structure notes, and suggested changes. There are no AI rating, ranking, winner, or adoption controls for this feedback.
- **Fact check:** one request verifies the chosen draft against the original project source text snapshotted in that run. Each claim is SUPPORTED, UNSUPPORTED, or UNCLEAR, with source IDs and exact excerpts where available. **UNSUPPORTED means not substantiated by the supplied sources, not false.** No external research, tools, or additional model responses are used as evidence.

Fact check requests strict API-level JSON Schema only when the selected catalog model advertises `structured_outputs`; `response_format` alone is not sufficient. Existing `require_parameters: true` and `allow_fallbacks: false` provider constraints remain in force. No extra capability lookup is made. Catalog metadata is advisory: a provider may still reject the schema or truncate output. Otherwise, the UI discloses prompt-only / best-effort compatibility before execution. Both paths make exactly one request, with no repair, retry, or model/provider fallback. Local Pydantic validation and grounding in a contiguous original source passage always remain required. Exact excerpt matching is preferred; a fallback permits differences only between existing ASCII whitespace runs (spaces, tabs, and line breaks), preserving all other characters and whitespace boundaries. On success, validated evidence stores the exact original passage; raw model output and immutable source snapshots stay unchanged. No case folding, Unicode normalization, fuzzy matching, or skipped intervening text is allowed. Separate source passages require separate references.

Optional runs appear in **Optional compute history**, separate from initial generation comparisons. Full input snapshots, selected response/version IDs and texts, prompts, model parameters, provider/request metadata, token usage, reported cost, and latency remain inspectable. Unknown values remain unknown. The existing 15-minute catalog freshness check, pricing heuristic, request claiming, timeouts, and no-retry behavior are reused.

Rewrite/merge candidates may be edited locally, then explicitly **Save / adopt candidate as current** creates a version, selects that candidate as primary, and sets the version current. It makes no API call. Original responses remain unchanged. Repeated adoption of the same candidate text reuses its saved version. Critique and fact-check results never change the current draft or version.

### Persistence and validation

No schema migration is required. M5 uses the existing run operation types, `runs.parent_run_id`, response kinds, `responses.structured_result_json`, and version derivation links. A single shared parent run is linked directly; merges spanning multiple runs retain all parent IDs in their selected-input snapshot instead of choosing an arbitrary parent. Adopted versions link to the candidate response; a single input version is also linked through `derived_from_version_id`. Multiple merge inputs remain recorded on the producing run.

`app/optional_prompts.py` contains the dedicated prompts and strict local Pydantic schemas. `app/optional_compute.py` validates input choices and builds requests using the shared generation planner. `app/optional_ui.py` provides the controls and result rendering; the existing executor and OpenRouter client perform the single request.

Malformed structured output is a failed validation outcome: its raw response, reported usage, and cost remain saved, and the UI shows an error. No repair or retry call occurs. Source verification also checks locally that cited source IDs exist in the run's snapshot, quoted excerpts exactly match those sources, and SUPPORTED claims contain a reference. These checks verify reference integrity, **not semantic entailment or real-world truth**. Model feedback still requires human judgment. Long/truncated output or models that do not follow the JSON instructions can fail validation.

Saved input context is authoritative except when the human explicitly chooses the current Working copy: that exact unsaved text is captured as the operation input, together with its saved response/version base. Unsaved candidate edits are browser-session state until adopted. No chaining, automatic adoption, automatic selection, external fact checking, or M6 redesign is implemented.
