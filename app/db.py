"""Small SQLite initialization and input workspace persistence functions."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

from app.types import OUTPUT_FORMATS, Project, ProjectCreate, Response, Run, RunRequest, Source

if TYPE_CHECKING:
    from app.openrouter import Completion

SCHEMA_VERSION = 1


@contextmanager
def connect(database_path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(database_path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_database(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(database_path) as connection:
        # Lock before reading the version so simultaneous app sessions cannot both initialize.
        connection.execute("BEGIN IMMEDIATE")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            return
        if version != 0:
            raise RuntimeError("Unsupported database schema version; use a compatible app version.")
        existing = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        if existing:
            raise RuntimeError("Refusing to initialize an unversioned database containing tables.")
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        # execute(), unlike executescript(), keeps the existing transaction intact.
        statement = ""
        for line in schema.splitlines(keepends=True):
            statement += line
            if sqlite3.complete_statement(statement):
                connection.execute(statement)
                statement = ""
        if statement.strip():
            raise RuntimeError("Incomplete schema statement.")
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def create_project(database_path: Path, name: str) -> Project:
    validated = ProjectCreate(name=name)
    now = datetime.now(UTC).isoformat()
    with connect(database_path) as connection:
        cursor = connection.execute(
            "INSERT INTO projects (name, created_at, updated_at) VALUES (?, ?, ?)",
            (validated.name, now, now),
        )
        row = connection.execute(
            "SELECT * FROM projects WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return Project.model_validate(dict(row))


def get_project(database_path: Path, project_id: int) -> Project | None:
    with connect(database_path) as connection:
        row = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return Project.model_validate(dict(row)) if row is not None else None


def list_projects(database_path: Path) -> list[Project]:
    with connect(database_path) as connection:
        rows = connection.execute("SELECT * FROM projects ORDER BY updated_at DESC, id DESC").fetchall()
        return [Project.model_validate(dict(row)) for row in rows]



def save_project_inputs(database_path: Path, project_id: int, task: str,
                        output_format: str, custom_output_format: str = '') -> None:
    if output_format not in OUTPUT_FORMATS:
        raise ValueError('Choose an available output format.')
    if output_format == 'Custom' and not custom_output_format.strip():
        raise ValueError('Describe the custom output format before saving.')
    with connect(database_path) as connection:
        cursor = connection.execute(
            'UPDATE projects SET task = ?, output_format = ?, custom_output_format = ?, updated_at = ? WHERE id = ?',
            (task, output_format, custom_output_format if output_format == 'Custom' else '',
             datetime.now(UTC).isoformat(), project_id),
        )
        if cursor.rowcount != 1:
            raise ValueError('Project no longer exists.')


def _save_source(database_path: Path, project_id: int, source_type: str, content: str,
                 title: str, original_url: str | None = None, final_url: str | None = None) -> Source:
    if not content.strip():
        raise ValueError('Source text cannot be empty.')
    now = datetime.now(UTC).isoformat()
    with connect(database_path) as connection:
        cursor = connection.execute(
            'INSERT INTO sources (project_id, type, title, content, original_url, final_url, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (project_id, source_type, title.strip(), content, original_url, final_url, now, now),
        )
        row = connection.execute('SELECT * FROM sources WHERE id = ?', (cursor.lastrowid,)).fetchone()
        return Source.model_validate(dict(row))


def add_text_source(database_path: Path, project_id: int, text: str, title: str = '') -> Source:
    return _save_source(database_path, project_id, 'text', text, title)


def add_webpage_source(database_path: Path, project_id: int, url: str) -> Source:
    from app.webpage import fetch_webpage

    page = fetch_webpage(url)
    return _save_source(database_path, project_id, 'webpage', page.content, page.title,
                        page.original_url, page.final_url)


def list_sources(database_path: Path, project_id: int) -> list[Source]:
    with connect(database_path) as connection:
        rows = connection.execute('SELECT * FROM sources WHERE project_id = ? ORDER BY id', (project_id,)).fetchall()
        return [Source.model_validate(dict(row)) for row in rows]


def remove_source(database_path: Path, project_id: int, source_id: int) -> None:
    with connect(database_path) as connection:
        connection.execute('DELETE FROM sources WHERE project_id = ? AND id = ?', (project_id, source_id))


def add_manual_response(database_path: Path, project_id: int, text: str,
                        claimed_provider: str = '', claimed_model: str = '') -> Response:
    if not text.strip():
        raise ValueError('Response text cannot be empty.')
    with connect(database_path) as connection:
        cursor = connection.execute(
            "INSERT INTO responses (project_id, text, origin, claimed_provider, claimed_model, created_at) VALUES (?, ?, 'manual', ?, ?, ?)",
            (project_id, text, claimed_provider.strip() or None, claimed_model.strip() or None,
             datetime.now(UTC).isoformat()),
        )
        row = connection.execute('SELECT * FROM responses WHERE id = ?', (cursor.lastrowid,)).fetchone()
        return Response.model_validate(dict(row))


def list_manual_responses(database_path: Path, project_id: int) -> list[Response]:
    with connect(database_path) as connection:
        rows = connection.execute("SELECT * FROM responses WHERE project_id = ? AND origin = 'manual' ORDER BY id", (project_id,)).fetchall()
        return [Response.model_validate(dict(row)) for row in rows]


def generation_inputs(database_path: Path, project_id: int) -> dict[str, Any]:
    """Read all prompt inputs in one SQLite snapshot, not from UI state."""
    with connect(database_path) as connection:
        connection.execute('BEGIN')
        row = connection.execute('SELECT * FROM projects WHERE id = ?', (project_id,)).fetchone()
        if row is None:
            raise ValueError('Project no longer exists.')
        sources = connection.execute('SELECT * FROM sources WHERE project_id = ? ORDER BY id', (project_id,)).fetchall()
        return {
            'project_id': project_id, 'project_name': row['name'], 'task': row['task'],
            'additional_instructions': row['additional_instructions'],
            'output_format': row['output_format'], 'custom_output_format': row['custom_output_format'],
            'sources': [dict(source) for source in sources],
        }


def create_generation_run(database_path: Path, snapshot: dict, parameters: dict,
                          prompt_version: str, estimated_cost: float | None) -> int:
    now = datetime.now(UTC).isoformat()
    project_id = snapshot['project_id']
    with connect(database_path) as connection:
        cursor = connection.execute(
            "INSERT INTO runs (project_id, operation, status, prompt_version, input_snapshot_json, parameters_json, estimated_cost_usd, created_at) "
            "VALUES (?, 'generation', 'pending', ?, ?, ?, ?, ?)",
            (project_id, prompt_version, json.dumps(snapshot), json.dumps(parameters), estimated_cost, now),
        )
        run_id = cursor.lastrowid
        connection.executemany(
            "INSERT INTO run_requests (project_id, run_id, requested_model, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
            [(project_id, run_id, model_id, now) for model_id in parameters['model_ids']],
        )
        connection.execute('UPDATE projects SET updated_at = ? WHERE id = ?', (now, project_id))
        return run_id


def claim_generation_run(database_path: Path, run_id: int) -> bool:
    """A persisted run can be dispatched at most once, even across concurrent callers."""
    with connect(database_path) as connection:
        return connection.execute(
            "UPDATE runs SET status = 'running' WHERE id = ? AND status = 'pending'", (run_id,),
        ).rowcount == 1


def mark_request_running(database_path: Path, request_id: int) -> None:
    with connect(database_path) as connection:
        connection.execute("UPDATE run_requests SET status = 'running' WHERE id = ? AND status = 'pending'", (request_id,))


def save_completion(database_path: Path, request_id: int, result: "Completion", metadata: dict) -> None:
    now = datetime.now(UTC).isoformat()
    with connect(database_path) as connection:
        request = connection.execute('SELECT * FROM run_requests WHERE id = ?', (request_id,)).fetchone()
        if request is None or request['status'] != 'running':
            raise ValueError('Request is not running; refusing to overwrite its outcome.')
        status = 'interrupted' if result.uncertain else 'failed' if result.error else 'succeeded'
        connection.execute(
            'UPDATE run_requests SET status = ?, error_message = ?, provider_request_id = ?, input_tokens = ?, '
            'output_tokens = ?, cost_usd = ?, latency_ms = ?, completed_at = ? WHERE id = ?',
            (status, result.error, result.request_id, result.input_tokens, result.output_tokens,
             result.cost_usd, result.latency_ms, now, request_id),
        )
        if result.error is None and result.text is not None:
            # Legacy verified_provider identifies the verified API gateway, never the model author.
            connection.execute(
                "INSERT INTO responses (project_id, run_request_id, text, origin, verified_provider, verified_model, "
                "finish_reason, generation_metadata_json, created_at) VALUES (?, ?, ?, 'api', 'openrouter', ?, ?, ?, ?)",
                (request['project_id'], request_id, result.text, result.model, result.finish_reason,
                 json.dumps(metadata, allow_nan=False), now),
            )


def finish_generation_run(database_path: Path, run_id: int) -> None:
    now = datetime.now(UTC).isoformat()
    with connect(database_path) as connection:
        # Also covers cooperative cancellation. A hard process kill remains visibly unfinished.
        connection.execute(
            "UPDATE run_requests SET status = 'interrupted', error_message = ?, completed_at = ? "
            "WHERE run_id = ? AND status IN ('pending', 'running')",
            ('Generation was interrupted; outcome and billing may be unknown. No automatic retry.', now, run_id),
        )
        statuses = [row[0] for row in connection.execute('SELECT status FROM run_requests WHERE run_id = ?', (run_id,))]
        status = ('succeeded' if all(value == 'succeeded' for value in statuses) else
                  'partial' if 'succeeded' in statuses else 'interrupted' if 'interrupted' in statuses else 'failed')
        connection.execute('UPDATE runs SET status = ?, completed_at = ? WHERE id = ?', (status, now, run_id))


def list_generation_runs(database_path: Path, project_id: int) -> list[Run]:
    with connect(database_path) as connection:
        rows = connection.execute(
            "SELECT * FROM runs WHERE project_id = ? AND operation = 'generation' ORDER BY id DESC", (project_id,),
        ).fetchall()
        return [Run.model_validate(dict(row)) for row in rows]


def get_generation_run(database_path: Path, run_id: int) -> Run:
    with connect(database_path) as connection:
        row = connection.execute("SELECT * FROM runs WHERE id = ? AND operation = 'generation'", (run_id,)).fetchone()
        if row is None:
            raise ValueError('Generation run not found.')
        return Run.model_validate(dict(row))


def list_run_requests(database_path: Path, run_id: int) -> list[RunRequest]:
    with connect(database_path) as connection:
        rows = connection.execute('SELECT * FROM run_requests WHERE run_id = ? ORDER BY id', (run_id,)).fetchall()
        requests = []
        for row in rows:
            response = connection.execute('SELECT * FROM responses WHERE run_request_id = ?', (row['id'],)).fetchone()
            requests.append(RunRequest.model_validate({
                **dict(row), 'response': dict(response) if response else None,
            }))
        return requests
