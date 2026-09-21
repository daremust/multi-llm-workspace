-- Schema version 1. Workflow tables are reserved foundations, not implemented features.
CREATE TABLE projects (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 200),
    task TEXT NOT NULL DEFAULT '',
    additional_instructions TEXT NOT NULL DEFAULT '',
    output_format TEXT NOT NULL DEFAULT 'Custom',
    custom_output_format TEXT NOT NULL DEFAULT '',
    current_version_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (id, current_version_id) REFERENCES versions(project_id, id)
);

CREATE TABLE sources (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    type TEXT NOT NULL CHECK (type IN ('text', 'webpage')),
    title TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    original_url TEXT,
    final_url TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE runs (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    operation TEXT NOT NULL CHECK (operation IN
        ('generation', 'regeneration', 'merge', 'rewrite', 'critique', 'fact_check')),
    parent_run_id INTEGER,
    status TEXT NOT NULL CHECK (status IN
        ('pending', 'running', 'succeeded', 'partial', 'failed', 'interrupted')),
    prompt_version TEXT NOT NULL,
    input_snapshot_json TEXT NOT NULL CHECK (json_valid(input_snapshot_json)),
    parameters_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(parameters_json)),
    estimated_cost_usd REAL CHECK (estimated_cost_usd >= 0),
    created_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE (project_id, id),
    FOREIGN KEY (project_id, parent_run_id) REFERENCES runs(project_id, id)
);

CREATE TABLE run_requests (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    run_id INTEGER NOT NULL,
    requested_model TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN
        ('pending', 'running', 'succeeded', 'failed', 'interrupted')),
    error_message TEXT,
    provider_request_id TEXT,
    input_tokens INTEGER CHECK (input_tokens >= 0),
    output_tokens INTEGER CHECK (output_tokens >= 0),
    cost_usd REAL CHECK (cost_usd >= 0),
    latency_ms INTEGER CHECK (latency_ms >= 0),
    created_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE (project_id, id),
    FOREIGN KEY (project_id, run_id) REFERENCES runs(project_id, id)
);

CREATE TABLE responses (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    run_request_id INTEGER UNIQUE,
    text TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'draft' CHECK (kind IN ('draft', 'critique', 'fact_check')),
    origin TEXT NOT NULL CHECK (origin IN ('api', 'manual')),
    verified_provider TEXT,
    verified_model TEXT,
    claimed_provider TEXT,
    claimed_model TEXT,
    finish_reason TEXT,
    generation_metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(generation_metadata_json)),
    structured_result_json TEXT CHECK (structured_result_json IS NULL OR json_valid(structured_result_json)),
    human_rating INTEGER CHECK (human_rating BETWEEN 1 AND 5),
    created_at TEXT NOT NULL,
    UNIQUE (project_id, id),
    FOREIGN KEY (project_id, run_request_id) REFERENCES run_requests(project_id, id),
    CHECK (
        (origin = 'manual' AND run_request_id IS NULL AND verified_provider IS NULL AND verified_model IS NULL)
        OR (origin = 'api' AND run_request_id IS NOT NULL AND verified_provider IS NOT NULL)
    )
);

CREATE TABLE versions (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    derived_from_response_id INTEGER,
    derived_from_version_id INTEGER,
    operation TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (project_id, id),
    FOREIGN KEY (project_id, derived_from_response_id) REFERENCES responses(project_id, id),
    FOREIGN KEY (project_id, derived_from_version_id) REFERENCES versions(project_id, id)
);

-- Small human-action history only: no replay or generalized event machinery.
CREATE TABLE project_events (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    event_type TEXT NOT NULL CHECK (event_type IN
        ('rating_changed', 'selection_changed', 'current_version_changed')),
    response_id INTEGER,
    version_id INTEGER,
    payload_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(payload_json)),
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id, response_id) REFERENCES responses(project_id, id),
    FOREIGN KEY (project_id, version_id) REFERENCES versions(project_id, id)
);

CREATE INDEX sources_project_idx ON sources(project_id);
CREATE INDEX run_requests_run_idx ON run_requests(run_id);
CREATE INDEX project_events_project_idx ON project_events(project_id);

