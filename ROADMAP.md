# Roadmap

## v0.1.0

The current release candidate provides:

- A local-first, single-user workspace.
- OpenRouter generation.
- Human-guided comparison and review.
- Optional Rewrite, Merge, Critique, and source-grounded Fact check.
- Local SQLite history.

## Post-v0.1.0 candidates

These deferred improvements are candidates for discussion, not release commitments:

- Copy clean with a preview and explicit user selection of removals.
- Optional autosave.
- Explicit user-initiated continuation for output-limited responses.
- Source titles and URLs alongside Fact check evidence.
- Investigate intermittent blank screens if the issue recurs.
- Automated browser navigation coverage and ongoing minimum Streamlit version testing.

## Potential future directions

These are possibilities for discussion, not commitments or scheduled releases.

- **Model access:** direct provider APIs and local models through tools such as Ollama.
- **Sources:** PDF, DOCX, TXT, Markdown, CSV, YouTube, GitHub, Drive, Docs, and Notion.
- **Verification:** broader external/web fact verification, beyond supplied sources.
- **Output:** export and integrations.
- **Editorial context:** editorial/distribution memory and duplicate-angle detection.
- **Evaluation:** optional advanced model-selection and evaluation workflows, with human judgment remaining authoritative.

Future work should preserve explicit control over additional compute and avoid automatic ranking or winner selection as defaults.
