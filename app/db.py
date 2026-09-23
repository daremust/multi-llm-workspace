"""Small SQLite initialization and input workspace persistence functions."""

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from app.types import OUTPUT_FORMATS, Project, ProjectCreate, Response, Source

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
