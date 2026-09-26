"""Explicit human review mutations. No model or network dependencies."""

import json
from datetime import UTC, datetime
from pathlib import Path

from app.db import connect
from app.types import Response, Version


def _response(connection, project_id: int, response_id: int):
    row = connection.execute(
        "SELECT r.* FROM responses r JOIN run_requests q ON q.id = r.run_request_id "
        "JOIN runs g ON g.id = q.run_id WHERE r.project_id = ? AND r.id = ? "
        "AND r.origin = 'api' AND q.status = 'succeeded' AND g.operation = 'generation'",
        (project_id, response_id),
    ).fetchone()
    if row is None:
        raise ValueError('Choose a successful generated response from this project.')
    return row


def _primary_id(connection, project_id: int) -> int | None:
    row = connection.execute(
        "SELECT response_id FROM project_events WHERE project_id = ? AND event_type = 'selection_changed' ORDER BY id DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    return row[0] if row else None


def get_primary_response(database_path: Path, project_id: int) -> Response | None:
    with connect(database_path) as connection:
        selected = _primary_id(connection, project_id)
        return Response.model_validate(dict(_response(connection, project_id, selected))) if selected is not None else None


def rate_response(database_path: Path, project_id: int, response_id: int, rating: int | None) -> None:
    if rating is not None and (type(rating) is not int or not 1 <= rating <= 5):
        raise ValueError('Human ratings must be between 1 and 5, or unrated.')
    with connect(database_path) as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = _response(connection, project_id, response_id)
        if row['human_rating'] == rating:
            return
        connection.execute('UPDATE responses SET human_rating = ? WHERE id = ?', (rating, response_id))
        connection.execute(
            "INSERT INTO project_events (project_id, event_type, response_id, payload_json, created_at) VALUES (?, 'rating_changed', ?, ?, ?)",
            (project_id, response_id, json.dumps({'human_rating': rating}), datetime.now(UTC).isoformat()),
        )


def select_primary_response(database_path: Path, project_id: int, response_id: int) -> None:
    with connect(database_path) as connection:
        connection.execute('BEGIN IMMEDIATE')
        _response(connection, project_id, response_id)
        if _primary_id(connection, project_id) == response_id:
            return
        connection.execute(
            "INSERT INTO project_events (project_id, event_type, response_id, payload_json, created_at) VALUES (?, 'selection_changed', ?, '{}', ?)",
            (project_id, response_id, datetime.now(UTC).isoformat()),
        )


def list_versions(database_path: Path, project_id: int) -> list[Version]:
    with connect(database_path) as connection:
        return [Version.model_validate(dict(row)) for row in connection.execute(
            'SELECT * FROM versions WHERE project_id = ? ORDER BY id DESC', (project_id,),
        )]


def _set_current(connection, project_id: int, version_id: int) -> None:
    row = connection.execute('SELECT current_version_id FROM projects WHERE id = ?', (project_id,)).fetchone()
    version = connection.execute('SELECT id FROM versions WHERE project_id = ? AND id = ?', (project_id, version_id)).fetchone()
    if row is None or version is None:
        raise ValueError('Choose a version belonging to this project.')
    if row['current_version_id'] == version_id:
        return
    now = datetime.now(UTC).isoformat()
    connection.execute('UPDATE projects SET current_version_id = ?, updated_at = ? WHERE id = ?', (version_id, now, project_id))
    connection.execute(
        "INSERT INTO project_events (project_id, event_type, version_id, payload_json, created_at) VALUES (?, 'current_version_changed', ?, '{}', ?)",
        (project_id, version_id, now),
    )


def set_current_version(database_path: Path, project_id: int, version_id: int) -> None:
    with connect(database_path) as connection:
        connection.execute('BEGIN IMMEDIATE')
        _set_current(connection, project_id, version_id)


def save_review_version(database_path: Path, project_id: int, response_id: int, text: str,
                        base_version_id: int | None = None) -> int:
    if not text.strip():
        raise ValueError('The saved version cannot be empty.')
    with connect(database_path) as connection:
        connection.execute('BEGIN IMMEDIATE')
        _response(connection, project_id, response_id)
        if _primary_id(connection, project_id) != response_id:
            raise ValueError('Primary selection changed. Reopen the selected response before saving.')
        if base_version_id is not None:
            base = connection.execute(
                'SELECT * FROM versions WHERE project_id = ? AND id = ? AND derived_from_response_id = ?',
                (project_id, base_version_id, response_id),
            ).fetchone()
            if base is None:
                raise ValueError('The editor base version does not belong to the selected response.')
        current = connection.execute(
            'SELECT v.* FROM projects p JOIN versions v ON v.id = p.current_version_id WHERE p.id = ?', (project_id,),
        ).fetchone()
        # Saving/finishing an unchanged current version is a no-op, not a duplicate edit.
        if current and current['derived_from_response_id'] == response_id and current['text'] == text:
            return current['id']
        version_id = connection.execute(
            "INSERT INTO versions (project_id, derived_from_response_id, derived_from_version_id, operation, text, created_at) "
            "VALUES (?, ?, ?, 'manual_edit', ?, ?)",
            (project_id, response_id, base_version_id, text, datetime.now(UTC).isoformat()),
        ).lastrowid
        _set_current(connection, project_id, version_id)
        return version_id
