# Multi-LLM Workspace

Compare multiple AI models, review their outputs, and refine your results with optional critique, source verification, merging, and rewriting.

**Multiple models for different perspectives. Additional compute only when you ask for depth.**

## Workflow

```text
Optional sources + Task & Output
↓
Generate with selected models
↓
Compare
↓
Human reviews / rates / selects / edits
↓
Finish — or explicitly run Rewrite, Merge, Critique, or Fact check
```

Human review is intentional: the application does not automatically rank models, choose a winner, critique, merge/synthesize, retry paid requests, or silently substitute models. Optional operations never run automatically.

## V0.1 features

- Local Streamlit application with System / Light / Dark appearance.
- Pasted-text and public webpage sources, freeform tasks/instructions, output presets and Custom format.
- On-demand OpenRouter model catalog and parallel generation with selected models (up to three concurrent requests).
- Side-by-side comparison, human star ratings, explicit Primary response selection, editable Working copy, and saved versions.
- Explicit Rewrite, Merge, Critique, and source-grounded Fact check.
- Per-request tokens, latency, and provider-reported cost where available.
- Local SQLite project history and manually pasted external-model responses with unverified provenance.

## Requirements

- **Python 3.12.x**. The current package requires `>=3.12,<3.13`; newer Python versions are not currently supported.
- Git and a modern web browser.
- An OpenRouter account and API key for model operations, including free models. Local project work and saved results need no key.

The v0.1.0 release candidate was tested with **Python 3.12.14 and Streamlit 1.51.0 / 1.64.0**, including automated tests and Chrome navigation checks. The declared requirements remain Python `>=3.12,<3.13` and Streamlit `>=1.51,<2`.

OpenRouter usage belongs to your own OpenRouter account. ChatGPT Plus, Claude Pro, and other consumer subscriptions do not pay for these API requests.

## Quick start — macOS / Linux

```sh
git clone https://github.com/daremust/multi-llm-workspace.git
cd multi-llm-workspace

python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e .

cp .env.example .env
```

Edit the local `.env` file and add your own key:

```dotenv
OPENROUTER_API_KEY=your_openrouter_api_key
```

Never commit your key. You can leave it blank to explore local project features.

From the repository root, with the environment activated:

```sh
streamlit run app/ui.py
```

Open [127.0.0.1:8501](http://127.0.0.1:8501) if the browser does not open automatically. The app binds to your computer's loopback address. Stop it with Ctrl+C; saved SQLite data remains available next time.

Create a project in the sidebar, save your Task & Output, optionally add sources, then explicitly load the catalog and choose models. Review the API call count, estimated cost, and saved-input preview before Generate. Generation uses saved inputs, excluding unsaved input edits and manual responses. A saved task and valid output format are required; Custom also requires saved format instructions. Sources are optional for generation but remain required for source-only Fact check.

If Python is not found, install Python 3.12 and rerun the commands. If a model operation is disabled, check the saved inputs, model selection, and API key. Restart Streamlit after changing environment configuration. If port 8501 is occupied, use `streamlit run app/ui.py --server.port 8502`.

## Configuration and local data

| Variable | Purpose |
| --- | --- |
| `OPENROUTER_API_KEY` | Required for explicit generation and Optional Compute. Not required for local work or the public catalog. |
| `WORKSPACE_DB_PATH` | Optional SQLite location; defaults to `data/workspace.db`. Relative paths resolve from the repository root; absolute paths and `~` are supported. |

Environment variables override values in the repository's `.env`. The database directory and schema are created on first startup. Each clone has its own database by default; pointing clones at the same custom path shares that data. To back up saved work, stop the app and copy the database. Unsaved Working copy edits are session-only; save a version before stopping.

The app and SQLite history run on your computer. **Local-first does not mean offline model requests:** explicit model operations send their selected prompts, source material, and draft inputs through OpenRouter to model providers. Loading the catalog and fetching a public webpage also require network access. Private/local webpage addresses are blocked.

Do not put secrets in sources or prompts you do not intend to send. Local database files contain source material, request snapshots, outputs, and review history. They and `.env` are ignored by Git.

Use Streamlit's appearance menu to select System, Light, or Dark. System is the default in a fresh browser; explicit browser preferences are retained.

Opening the bare app URL shows Home. Project URLs use `#project-<ID>` (for example, `http://127.0.0.1:8501/#project-3`); opening or refreshing a valid URL restores that project. Selecting a project updates the hash, and Home clears it. Invalid, deleted, or legacy name-based hashes fall back to Home and are cleared. IDs refer to projects in the local database; links are not portable between unrelated databases. Renaming a project does not change its ID-based URL.

Navigation uses a browser component and may briefly show “Opening workspace…” during initialization. Project switches replace the hash without adding browser-history entries. Unsaved drafts and model selections remain session state: Home/project switching preserves working drafts within the session, but refresh does not. Save a version before refreshing. The former deprecated `components.html()` hash-sync iframe has been replaced by the v2 navigation component.

## Compare and finish

Use **☆ Favorites** beside Models to star or unstar a model without selecting it or making a request. Favorites appear first; all other eligible models remain available in catalog order. Unavailable favorite IDs are retained and can be unstarred in the popover. Existing pricing filters still apply.

Favorites are shared across projects using the same database and persist in `<database filename>.favorites.json` beside it (for example, `data/workspace.db.favorites.json`). Copy this sidecar with the database to back up or transfer favorites. Separate database paths have separate favorites. Malformed or unreadable preferences show an error and are not overwritten. Avoid simultaneous favorite edits in multiple tabs; competing writes can overwrite one another.

Compare keeps failed and interrupted requests visible alongside successful responses. Ratings never reorder outputs or select a Primary response.

When available, **Provider-reported sources** displays citation links separately from project sources, in Compare and through the originating response in Final. Individual citation-marker mappings are not assumed, and these links do not automatically support edited or merged content.

Compare warns “Output limit reached — this response may be incomplete.” when the saved provider finish reason is `length`. Original response text remains unchanged.

Select a Primary response explicitly, edit its Working copy, and save a version. Original responses remain unchanged. **Finish / Save** saves the Current version locally; it does not publish anything. Repeating a save on unchanged current text does not duplicate the version.

Primary response selects the response to edit; Current version identifies the saved version currently selected in history. Selecting a Primary response alone does not change the Current version. Explicitly restoring a saved version also aligns Primary with that version's originating response and starts the editor from the restored text. Returning Home or switching projects preserves unsaved working edits within the session; a fresh session requires saved versions.

Manual external-model responses are stored with unverified provenance. Their external API usage and cost are not tracked by this application.

### Copying responses and drafts

Compare has a copy icon beside each successful model response; it copies only the full original response text. **Select to copy** chooses responses for **Copy selected**; **Copy all** copies every successful response in the displayed run. Bulk copies include model labels and exclude failed responses. The optional **Include original task and sources** adds that run's saved task, output format, source titles, available URLs, and source text from its historical snapshot. It defaults to off and does not affect individual copy icons. Copy selections are separate from ratings and Optional Compute.

Final's **Copy** reads the current working-copy editor, including unsaved typing. Copy buttons run locally in the browser without model requests or version saves. “Copied” appears only after the browser confirms success; denied clipboard access shows a manual-copy instruction. Use localhost or HTTPS and allow clipboard access when requested. Selecting copy options causes an ordinary UI rerun; clipboard buttons do not request one.

## Optional Compute — only when needed

Choose the operation, eligible inputs, and model yourself. Each operation adds **one API call**, with its estimated additional cost shown before execution.

- **Rewrite:** use the Working copy (including unsaved edits) or Current version with optional instructions.
- **Merge:** choose at least two successful API draft responses or saved versions.
- **Critique:** structured qualitative feedback on the chosen draft and saved task/output context.
- **Fact check:** inspect claims against the original supplied sources snapshotted for that run.

Rewrite and Merge produce candidates. Only explicit **Save / adopt candidate as current** selects a candidate as Primary response and saves it as the Current version. Critique and Fact check do not alter your draft, version, ratings, or selection. Operations are never chained or adopted automatically.

### Fact check limitations

- **SUPPORTED:** supporting source evidence was identified by the model.
- **UNSUPPORTED:** not substantiated by supplied sources; this does **not** automatically mean false.
- **UNCLEAR:** source evidence is insufficient or ambiguous.

V0.1 Fact check is not general web verification. Model agreement does not establish truth.

Local validation checks the schema, source IDs, and contiguous source excerpts against immutable source snapshots. Exact matching is preferred; differences only between existing ASCII whitespace runs are accepted and mapped back to the exact original passage. Punctuation, wording, Unicode differences, and stitched passages are not accepted. This verifies reference integrity, not semantic entailment or real-world truth.

API-level JSON Schema is requested only when catalog metadata advertises structured-output support. Otherwise the UI discloses prompt-only / best-effort compatibility. Both paths retain local validation and use one request: no repair, retry, or model/provider fallback. Invalid or truncated results fail safely; available raw output, usage, and cost remain inspectable. The default output limit is 4,096 tokens for Fact check and 1,024 for other operations, and can be changed manually.

## Costs and request outcomes

Initial generation makes one request per selected model. Optional Compute adds its own request. Estimates use catalog prices, request fees, an approximate input-token count, and the configured output limit. They are **not a spending cap**; tokenization, reasoning, routing, fees, and prices can differ.

Generate, its collapsed summary, and Optional Compute show a base-rate token estimate whenever the selected models have valid input/output token prices. This excludes fee and pricing-tier adjustments. Missing or malformed pricing, unresolved fees, or tier overrides keep the total unknown; a warning is shown, and generation remains available with valid inputs and an API key.

Actual/provider-reported usage and cost are shown when available. **Cost unknown stays unknown, never zero.** Free-token pricing does not guarantee that all fees are known or that a model is available.

Catalog loading is explicit and cached for 15 minutes. Execution requires a fresh catalog. UI reruns do not automatically refresh the catalog or rerun completed operations.

Failed, Partial, and Interrupted / uncertain outcomes are distinct. A timeout or interruption may leave completion and billing uncertain. A hard process stop can leave a run unfinished; the app does not automatically resume or retry it. Successful sibling responses remain available.

## Development and tests

After the user setup, install test dependencies separately:

```sh
python -m pip install -e ".[dev]"
python -m pytest
git diff --check
```

Tests use temporary SQLite databases and mocked API responses. Outbound network access is blocked during tests; no real API key or paid requests are required.

The clipboard JavaScript lifecycle harness runs **separately from pytest**. It requires a modern Node.js runtime supporting `Array.prototype.at()` and the activated Python environment above; no npm packages are needed. From the repository root, pipe the actual component source into the harness:

```sh
python -c 'from app.clipboard_ui import COPY_JS; print(COPY_JS)' | node tests/clipboard_lifecycle.js
```

The harness uses DOM and clipboard doubles to check repeated rendering, cleanup, current payloads, and success/failure feedback. It does not verify real browser permissions; manually test clipboard actions, including unsaved Final edits, in your browser. Node.js is only needed for this harness, not to run the application.

## Repository structure

- `app/ui.py`, `app/*_ui.py`, `app/ui_style.py`: Streamlit interface and shared presentation.
- `app/openrouter.py`, `app/generation.py`: catalog, requests, estimates, and execution.
- `app/review.py`, `app/optional_compute.py`, `app/optional_prompts.py`: human review and explicit optional operations.
- `app/db.py`, `app/schema.sql`: local SQLite persistence.
- `tests/`: automated coverage.
- `.streamlit/config.toml`, `.env.example`: appearance/local-server defaults and configuration template.

### Webpage extraction limits

New webpage fetches prefer a substantive article (at least 200 characters of non-link text), then main content, then a cleaned body fallback. Clearly marked navigation, related posts, sharing controls, comments, and hidden elements are excluded. Headings, list items, code indentation, table-cell separators, captions, and HTTP(S) article links are retained. Relative links use the final fetched URL; link targets are not fetched. Repeated prose is not deduplicated.

Extraction is structural, not a browser rendering or a universal readability engine. Unmarked boilerplate, unusual markup, multiple substantial articles, and JavaScript-only pages may need manual review or pasted source text. CSS stylesheets are not evaluated. Previously saved sources and historical run snapshots are never rewritten by an extractor update.
