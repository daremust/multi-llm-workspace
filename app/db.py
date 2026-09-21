"""Small SQLite initialization and project persistence functions."""

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from app.types import Project, ProjectCreate

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

